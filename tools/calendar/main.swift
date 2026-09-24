// sounder のカレンダー連携の補助アプリ（SounderCalendar.app）。
//
// macOS のカレンダー（EventKit）から、これからの予定を読んで JSON に書き出すだけ。
// iCloud の共有カレンダーなど、カレンダー.app に出ているものはそのまま読める。ネットワークには出ない。
// LaunchAgent（com.local.sounder-calendar）が 5 分ごとに起動する。sounder はその JSON を読む。
//
//   SounderCalendar --out data/calendar.json [--days 8]   予定を書き出す
//   SounderCalendar --request                              カレンダーへのアクセス許可を求める
import EventKit
import Foundation

let args = CommandLine.arguments
func value(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

let store = EKEventStore()
let iso = ISO8601DateFormatter()
iso.formatOptions = [.withInternetDateTime]
iso.timeZone = TimeZone.current

func statusName(_ s: EKAuthorizationStatus) -> String {
    switch s {
    case .fullAccess: return "authorized"
    case .writeOnly: return "write_only"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

func requestAccess() -> Bool {
    let sem = DispatchSemaphore(value: 0)
    var granted = false
    store.requestFullAccessToEvents { ok, _ in
        granted = ok
        sem.signal()
    }
    sem.wait()
    return granted
}

func hex(_ c: CGColor?) -> String? {
    guard let c = c, let comps = c.converted(to: CGColorSpace(name: CGColorSpace.sRGB)!, intent: .defaultIntent, options: nil)?.components,
          comps.count >= 3 else { return nil }
    return String(format: "#%02x%02x%02x", Int(comps[0] * 255), Int(comps[1] * 255), Int(comps[2] * 255))
}

func write(_ obj: [String: Any], to path: String) {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
    let url = URL(fileURLWithPath: path)
    try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let tmp = url.appendingPathExtension("part")
    try! data.write(to: tmp)
    _ = try? FileManager.default.replaceItemAt(url, withItemAt: tmp)
    if !FileManager.default.fileExists(atPath: path) { try? FileManager.default.moveItem(at: tmp, to: url) }
}

if args.contains("--request") {
    let ok = requestAccess()
    print(ok ? "authorized" : statusName(EKEventStore.authorizationStatus(for: .event)))
    exit(ok ? 0 : 1)
}

guard let out = value("--out") else {
    FileHandle.standardError.write("使い方: SounderCalendar --out <path> [--days N] | --request\n".data(using: .utf8)!)
    exit(2)
}
let days = Int(value("--days") ?? "8") ?? 8

var status = EKEventStore.authorizationStatus(for: .event)
if status == .notDetermined {
    _ = requestAccess()
    status = EKEventStore.authorizationStatus(for: .event)
}
var result: [String: Any] = [
    "generated": iso.string(from: Date()),
    "status": statusName(status),
    "calendars": [],
    "events": [],
]
if status == .fullAccess {
    let calendars = store.calendars(for: .event)
    result["calendars"] = calendars.map { cal -> [String: Any] in
        var d: [String: Any] = ["id": cal.calendarIdentifier, "title": cal.title,
                                "source": cal.source.title]
        if let c = hex(cal.cgColor) { d["color"] = c }
        return d
    }
    let start = Calendar.current.startOfDay(for: Date())
    let end = Calendar.current.date(byAdding: .day, value: days, to: start)!
    let events = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: calendars))
    result["events"] = events.sorted { $0.startDate < $1.startDate }.map { ev -> [String: Any] in
        var d: [String: Any] = [
            "id": "\(ev.calendarItemIdentifier)|\(iso.string(from: ev.startDate))",
            "calendar_id": ev.calendar.calendarIdentifier,
            "title": ev.title ?? "",
            "start": iso.string(from: ev.startDate),
            "end": iso.string(from: ev.endDate),
            "all_day": ev.isAllDay,
        ]
        if let loc = ev.location, !loc.isEmpty { d["location"] = loc }
        return d
    }
}
write(result, to: out)
print("\(result["status"]!): \((result["events"] as! [Any]).count) 件")
