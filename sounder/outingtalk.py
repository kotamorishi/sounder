"""お出かけの読み上げを、毎日少しずつ変える（任意）。

- 言い回し: AI 連携がオンなら、天気・服装・昨日との比べなどの決まった文を、毎朝違う言い方に AI に直してもらう。
  数字や服装の中身は変えさせない。間に合わなければ決まった文のまま読む。
- 今日のフランス語: 単語を 1 つ英語で説明し、例文を読む。単語と例文はフランス語の声で読む。
  AI があれば今週の行事（ハロウィン・祝日・PA デーなど）や今日の天気にちなんだ単語と、
  その話題の例文 2 つを作らせる。無ければ決まった一覧から日替わりで選ぶ。

どちらも、その朝いちばん最初のお知らせ（いちばん早い予告、予告が無ければ本番）にだけ付ける。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import daysoff

DEFAULTS = {"rephrase": True, "french": False, "french_voice": "Amélie", "english_voice": ""}
# 何秒前から AI に文を作らせ始めるか（読み上げの音声は 3 分前から作るので、それより前に終わらせる）
AHEAD = 15 * 60
# 最近使った単語をいくつ覚えておくか（AI に同じ単語を選ばせない）
KEEP_WORDS = 120
# 例文をいくつまで読むか
MAX_EXAMPLES = 2
# フランス語を何回ずつ読むか（英語は 1 回で足りるが、フランス語は何度か聞きたい）
WORD_TIMES = 3
EXAMPLE_TIMES = 2
# 何日先までの行事を AI に伝えるか
EVENT_DAYS = 7


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(weekday - d.weekday()) % 7 + 7 * (n - 1))


def seasonal_events(year: int) -> dict[date, str]:
    """祝日ではないが、子どもの話題になる行事（例文の手がかりにする）。"""
    return {
        date(year, 2, 2): "グラウンドホッグ・デー",
        date(year, 2, 14): "バレンタインデー",
        date(year, 3, 17): "セント・パトリックス・デー",
        date(year, 4, 22): "アースデー",
        _nth_weekday(year, 5, 6, 2): "母の日",
        _nth_weekday(year, 6, 6, 3): "父の日",
        date(year, 10, 31): "ハロウィン",
        date(year, 11, 11): "リメンブランス・デー",
        date(year, 12, 24): "クリスマス・イブ",
        date(year, 12, 31): "大みそか",
    }


def upcoming_events(day: date, days: int = EVENT_DAYS) -> list[str]:
    """今日から days 日のあいだの行事・祝日・学校の休み（「10月31日（土）ハロウィン」の形）。"""
    out, seen = [], set()
    for i in range(days + 1):
        d = day + timedelta(days=i)
        names = [n for n in (seasonal_events(d.year).get(d), daysoff.ontario_holidays(d.year).get(d)) if n]
        school = daysoff.reason("tdsb_elementary", d)
        # 冬休みなど何日も続く休みは、最初の日にだけ書く
        if school and school != "週末" and school not in names and school not in seen:
            names.append(f"学校は{school}で休み")
        seen.add(school)
        if names:
            out.append(f"{d.month}月{d.day}日（{'月火水木金土日'[d.weekday()]}）{'・'.join(names)}")
    return out

# AI が使えないときの日替わりの単語（単語・英語の意味・例文・例文の英訳）
WORDS = [
    ("le soleil", "the sun", "Le soleil est chaud.", "The sun is hot."),
    ("la pluie", "the rain", "J'aime la pluie.", "I like the rain."),
    ("la neige", "the snow", "La neige est blanche.", "The snow is white."),
    ("le nuage", "the cloud", "Le nuage est gris.", "The cloud is grey."),
    ("le vent", "the wind", "Le vent est fort.", "The wind is strong."),
    ("le parapluie", "the umbrella", "J'ai un parapluie rouge.", "I have a red umbrella."),
    ("le manteau", "the coat", "Mets ton manteau.", "Put on your coat."),
    ("le chapeau", "the hat", "Mon chapeau est bleu.", "My hat is blue."),
    ("les gants", "the gloves", "J'ai froid aux mains, je mets mes gants.", "My hands are cold, I put on my gloves."),
    ("les bottes", "the boots", "Mes bottes sont mouillées.", "My boots are wet."),
    ("l'écharpe", "the scarf", "Mon écharpe est longue.", "My scarf is long."),
    ("froid", "cold", "Il fait froid aujourd'hui.", "It is cold today."),
    ("chaud", "hot or warm", "Il fait chaud dehors.", "It is warm outside."),
    ("l'école", "the school", "Je vais à l'école.", "I am going to school."),
    ("le sac à dos", "the backpack", "Mon sac à dos est lourd.", "My backpack is heavy."),
    ("le crayon", "the pencil", "J'ai un crayon jaune.", "I have a yellow pencil."),
    ("le livre", "the book", "Je lis un livre.", "I am reading a book."),
    ("le professeur", "the teacher", "Le professeur est gentil.", "The teacher is kind."),
    ("l'ami", "the friend", "Mon ami s'appelle Léo.", "My friend's name is Léo."),
    ("la récréation", "recess", "On joue à la récréation.", "We play at recess."),
    ("le dîner", "lunch (in Canada)", "Mon dîner est dans mon sac.", "My lunch is in my bag."),
    ("la pomme", "the apple", "Je mange une pomme.", "I am eating an apple."),
    ("le fromage", "the cheese", "J'aime le fromage.", "I like cheese."),
    ("le pain", "the bread", "Le pain est bon.", "The bread is good."),
    ("l'eau", "the water", "Je bois de l'eau.", "I drink water."),
    ("le lait", "the milk", "Je bois du lait.", "I drink milk."),
    ("le chat", "the cat", "Le chat dort.", "The cat is sleeping."),
    ("le chien", "the dog", "Le chien court vite.", "The dog runs fast."),
    ("l'oiseau", "the bird", "L'oiseau chante.", "The bird is singing."),
    ("l'arbre", "the tree", "L'arbre est grand.", "The tree is tall."),
    ("la feuille", "the leaf", "La feuille est orange.", "The leaf is orange."),
    ("la fleur", "the flower", "La fleur sent bon.", "The flower smells nice."),
    ("rouge", "red", "La pomme est rouge.", "The apple is red."),
    ("bleu", "blue", "Le ciel est bleu.", "The sky is blue."),
    ("vert", "green", "L'herbe est verte.", "The grass is green."),
    ("jaune", "yellow", "Le soleil est jaune.", "The sun is yellow."),
    ("bonjour", "hello", "Bonjour, madame!", "Hello, ma'am!"),
    ("merci", "thank you", "Merci beaucoup!", "Thank you very much!"),
    ("s'il vous plaît", "please", "Un verre d'eau, s'il vous plaît.", "A glass of water, please."),
    ("au revoir", "goodbye", "Au revoir, à demain!", "Goodbye, see you tomorrow!"),
    ("aujourd'hui", "today", "Aujourd'hui, c'est vendredi.", "Today is Friday."),
    ("demain", "tomorrow", "À demain!", "See you tomorrow!"),
    ("le matin", "the morning", "Le matin, je mange des céréales.", "In the morning, I eat cereal."),
    ("la maison", "the house", "Ma maison est petite.", "My house is small."),
    ("la famille", "the family", "J'aime ma famille.", "I love my family."),
    ("le frère", "the brother", "Mon frère a huit ans.", "My brother is eight years old."),
    ("la sœur", "the sister", "Ma sœur aime danser.", "My sister likes to dance."),
    ("courir", "to run", "J'aime courir.", "I like to run."),
    ("jouer", "to play", "On va jouer dehors.", "We are going to play outside."),
    ("manger", "to eat", "Je veux manger.", "I want to eat."),
    ("dormir", "to sleep", "Le bébé va dormir.", "The baby is going to sleep."),
    ("heureux", "happy", "Je suis heureux.", "I am happy."),
    ("fatigué", "tired", "Je suis fatigué ce matin.", "I am tired this morning."),
    ("vite", "fast or quickly", "Vite, on est en retard!", "Quick, we are late!"),
    ("lentement", "slowly", "La tortue marche lentement.", "The turtle walks slowly."),
    ("l'autobus", "the bus", "L'autobus est jaune.", "The bus is yellow."),
    ("le vélo", "the bike", "Je fais du vélo.", "I ride my bike."),
    ("la citrouille", "the pumpkin", "La citrouille est orange.", "The pumpkin is orange."),
    ("le flocon", "the snowflake", "Un flocon tombe sur mon nez.", "A snowflake falls on my nose."),
    ("le bonhomme de neige", "the snowman", "On fait un bonhomme de neige.", "We are making a snowman."),
]


def settings_of(settings: dict) -> dict:
    return {**DEFAULTS, **(settings.get("outing") or {})}


def word_of_the_day(day: date) -> dict:
    """AI を使わないときの日替わりの単語。"""
    i = day.toordinal() % len(WORDS)
    w, meaning, ex, ex_en = WORDS[i]
    return {"word": w, "meaning": meaning, "examples": [{"fr": ex, "en": ex_en}]}


def examples_of(fr: dict) -> list[dict]:
    """例文の並び（[{"fr", "en"}]）。前の形（example・example_en が 1 つ）も読む。"""
    exs = fr.get("examples")
    if exs is None and fr.get("example"):
        exs = [{"fr": fr["example"], "en": fr.get("example_en") or ""}]
    return [e for e in exs or [] if isinstance(e, dict) and e.get("fr")][:MAX_EXAMPLES]


def french_parts(fr: dict, settings: dict, voice: str) -> list[dict]:
    """「今日のフランス語」の読み上げ（英語の説明は英語の声で 1 回、単語と例文はフランス語の声で何回か）。"""
    cfg = settings_of(settings)
    en, fv = cfg["english_voice"] or voice, cfg["french_voice"] or voice
    parts = [{"text": "Today's French word is", "voice": en}]
    parts += [{"text": fr["word"], "voice": fv}] * WORD_TIMES
    parts.append({"text": f"It means {fr['meaning']}.", "voice": en})
    for ex in examples_of(fr):
        parts += [{"text": ex["fr"], "voice": fv}] * EXAMPLE_TIMES
        if ex.get("en"):
            parts.append({"text": ex["en"], "voice": en})
    return parts


class OutingTalk:
    def __init__(self, ai, path: Path, *, log=None) -> None:
        self.ai = ai                      # ai.AI（complete と設定を使う）
        self.path = path                  # 最近使った単語と、前の日の言い方を覚えておくファイル
        self._log = log or (lambda *a, **k: None)
        self._lock = threading.Lock()
        self._done: dict[str, dict] = {}  # 鍵 → {"talk", "french"}
        self._busy: set[str] = set()
        self._memory: dict | None = None

    def _mem(self) -> dict:
        if self._memory is None:
            try:
                self._memory = json.loads(self.path.read_text("utf-8"))
            except (OSError, ValueError):
                self._memory = {}
        return self._memory

    def _save(self) -> None:
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._mem(), ensure_ascii=False), "utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

    def _ai_on(self, settings: dict) -> bool:
        cfg = (settings.get("ai") or {})
        return bool(self.ai and cfg.get("enabled"))

    def get(self, settings: dict, key: str, facts: str, day: date, voice: str) -> tuple[str, list[dict]]:
        """読み上げる文と、続けて読む「今日のフランス語」を返す。AI の答えがまだなら作り始めて、決まった文を返す。"""
        cfg = settings_of(settings)
        use_ai = self._ai_on(settings) and (cfg["rephrase"] or cfg["french"])
        got = None
        if use_ai:
            key = hashlib.sha256(f"{key}|{cfg['rephrase']}|{cfg['french']}".encode()).hexdigest()[:16]
            with self._lock:
                got = self._done.get(key)
                start = got is None and key not in self._busy
                if start:
                    self._busy.add(key)
            if start:
                threading.Thread(target=self._generate, args=(settings, key, facts, day),
                                 name="outing-talk", daemon=True).start()
        talk = (got or {}).get("talk") if cfg["rephrase"] else None
        parts = []
        if cfg["french"]:
            fr = (got or {}).get("french") or word_of_the_day(day)
            parts = french_parts(fr, settings, voice)
        return talk or facts, parts

    def prompt(self, settings: dict, facts: str, day: date) -> str:
        cfg = settings_of(settings)
        mem = self._mem()
        lines = [f"今日は{day.year}年{day.month}月{day.day}日（{'月火水木金土日'[day.weekday()]}曜日）です。"]
        if cfg["rephrase"]:
            lines += [
                "朝、子どもが学校へ出かける前に、家のスピーカーで読み上げる「天気と服装のお知らせ」があります。",
                f"いつもの文: {facts}",
            ]
            if mem.get("last_talk"):
                lines.append(f"前回の言い方（これと似た言い回しにしない）: {mem['last_talk']}")
            lines.append(
                "talk には、いつもの文を毎朝同じに聞こえないよう言い換えた文を入れてください。"
                "数字・時刻・服装・天気・季節の話の中身は変えず、足さず、削らない（順番は変えてよい）。"
                "「寒い」を「とても寒い」にするなど、程度を強めたり弱めたりもしない。"
                "子どもにも分かるやさしい日本語の話し言葉で、記号・絵文字・かっこ書きは使わない。200字以内。")
        if cfg["french"]:
            recent = "、".join(mem.get("words") or []) or "なし"
            events = upcoming_events(day)
            if not cfg["rephrase"]:
                lines.append(f"今日の天気と服装: {facts}")
            lines.append("今週の行事・祝日・学校の休み: " + ("、".join(events) if events else "特になし"))
            lines.append(
                "french には「今日のフランス語」を入れてください。聞くのはカナダ（オンタリオ州）の小学生で、"
                "フランス語の授業を受けていて、簡単な単語はもう知っています。"
                "単語は 1 つ。今週の行事（上の一覧）・季節・今日の天気など、いまの話題にちなんだものを選ぶ。"
                "名詞は冠詞つき（l' の場合も）。"
                f"最近使った単語は避ける: {recent}。"
                "meaning は英語の意味（短く）。"
                f"examples には、その単語を使ったフランス語の例文を {MAX_EXAMPLES} つ（fr）と、それぞれの英訳（en）を入れる。"
                "例文は辞書の例ではなく、今日・今週の話題に結びつける"
                "（例: ハロウィンの週の la citrouille なら「Ce week-end, tu vas voir beaucoup de citrouilles dehors.」"
                "「Pour l'Halloween, les gens décorent leur maison avec des citrouilles.」）。"
                "1 文は 12 語くらいまで。小学生に分かる言葉で、ふつうの会話の文にする。")
        keys = ", ".join(k for k, on in (('"talk": "…"', cfg["rephrase"]),
                                          ('"french": {"word", "meaning", "examples": [{"fr", "en"}]}', cfg["french"]))
                         if on)
        lines.append(f"JSON だけを出力してください（{{{keys}}}）。前置きやコードブロックは付けない。")
        return "\n".join(lines)

    @staticmethod
    def parse(text: str) -> dict:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("JSON がありません")
        d = json.loads(text[i:j + 1])
        out = {}
        if isinstance(d.get("talk"), str) and d["talk"].strip():
            out["talk"] = d["talk"].strip()[:400]
        fr = d.get("french")
        if isinstance(fr, dict) and all(isinstance(fr.get(k), str) and fr[k].strip() for k in ("word", "meaning")):
            out["french"] = {k: fr[k].strip()[:120] for k in ("word", "meaning")}
            out["french"]["examples"] = [{"fr": str(e.get("fr") or "").strip()[:200],
                                          "en": str(e.get("en") or "").strip()[:200]}
                                         for e in examples_of(fr)]
        return out

    def _generate(self, settings: dict, key: str, facts: str, day: date) -> None:
        t = time.time()
        try:
            got = self.parse(self.ai.complete(settings, self.prompt(settings, facts, day), max_tokens=1200))
            self._log("info", f"AI がお出かけの読み上げを作りました（{time.time() - t:.1f} 秒）")
        except Exception as exc:   # AI が使えなくても、決まった文で読めばよい
            got = {}
            self._log("error", f"AI でお出かけの読み上げを作れなかったので、決まった文で読みます（{exc}）")
        with self._lock:
            self._done = {k: v for k, v in list(self._done.items())[-20:]}
            self._done[key] = got
            self._busy.discard(key)
            mem = self._mem()
            if got.get("talk"):
                mem["last_talk"] = got["talk"]
            if got.get("french"):
                mem["words"] = ((mem.get("words") or []) + [got["french"]["word"]])[-KEEP_WORDS:]
            self._save()

def first_of_the_morning(sched: dict, tag: str, lead: int) -> bool:
    """その回のお知らせのうち、いちばん最初に鳴るものか（いちばん早い予告、予告が無ければ本番）。"""
    leads = sched.get("lead_times") or []
    if leads:
        return tag == "lead" and lead == max(leads)
    return tag == "main"


def when_key(sched: dict, leave: datetime) -> str:
    return f"{sched.get('id')}|{leave:%Y-%m-%d %H:%M}"
