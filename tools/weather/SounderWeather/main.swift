// sounder の天気の補助アプリ（SounderWeather.app）。
//
// WeatherKit で、設定した地点の今の天気と今日・明日の予報を JSON に書き出すだけ。
// 地点は sounder の設定（data/config.json の settings.weather.lat / lon / name）から読むので、
// 位置情報の許可はいらない。LaunchAgent（com.local.sounder-weather）が 30 分ごとに起動する。
//
//   SounderWeather --config data/config.json --out data/weather.json
//   SounderWeather --geocode "Toronto"      地名 → 緯度・経度の候補（JSON）
//
// WeatherKit を使うには、Apple Developer の App ID で WeatherKit（Capabilities と App Services）を
// オンにして、そのチームで署名する必要がある（scripts/install-weather.sh）。
import CoreLocation
import Foundation
import WeatherKit

let args = CommandLine.arguments
func value(_ name: String) -> String? {
    guard let i = args.firstIndex(of: name), i + 1 < args.count else { return nil }
    return args[i + 1]
}

let iso = ISO8601DateFormatter()
iso.formatOptions = [.withInternetDateTime]
iso.timeZone = TimeZone.current

func write(_ obj: [String: Any], to path: String) {
    let data = try! JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])
    let url = URL(fileURLWithPath: path)
    try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
    let tmp = url.appendingPathExtension("part")
    try! data.write(to: tmp)
    if FileManager.default.fileExists(atPath: path) {
        _ = try? FileManager.default.replaceItemAt(url, withItemAt: tmp)
    } else {
        try? FileManager.default.moveItem(at: tmp, to: url)
    }
}

func celsius(_ m: Measurement<UnitTemperature>) -> Double {
    (m.converted(to: .celsius).value * 10).rounded() / 10
}

func day(_ d: DayWeather) -> [String: Any] {
    [
        "date": iso.string(from: d.date),
        "condition": d.condition.rawValue,
        "description": d.condition.description,
        "symbol": d.symbolName,
        "high_c": celsius(d.highTemperature),
        "low_c": celsius(d.lowTemperature),
        "precipitation_chance": (d.precipitationChance * 100).rounded(),
        "precipitation": d.precipitation.rawValue,
        "uv_index": d.uvIndex.value,
        "sunrise": d.sun.sunrise.map { iso.string(from: $0) } ?? NSNull(),
        "sunset": d.sun.sunset.map { iso.string(from: $0) } ?? NSNull(),
    ]
}

// 地名から緯度・経度を探す（設定画面の「場所を探す」用）。Apple の地名検索を使う。位置情報の許可はいらない
if let query = value("--geocode") {
    var found: [[String: Any]] = []
    do {
        let marks = try await CLGeocoder().geocodeAddressString(query)
        for m in marks.prefix(5) {
            guard let loc = m.location else { continue }
            let name = [m.locality, m.administrativeArea, m.country].compactMap { $0 }.joined(separator: ", ")
            found.append(["name": name.isEmpty ? query : name,
                          "lat": (loc.coordinate.latitude * 10000).rounded() / 10000,
                          "lon": (loc.coordinate.longitude * 10000).rounded() / 10000])
        }
    } catch {}
    let data = try! JSONSerialization.data(withJSONObject: found, options: [])
    print(String(data: data, encoding: .utf8)!)
    exit(0)
}

guard let out = value("--out"), let configPath = value("--config") else {
    FileHandle.standardError.write("使い方: SounderWeather --config <config.json> --out <weather.json> | --geocode <地名>\n".data(using: .utf8)!)
    exit(2)
}

var result: [String: Any] = ["generated": iso.string(from: Date())]
let config = (try? JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: configPath)))) as? [String: Any]
let weather = (config?["settings"] as? [String: Any])?["weather"] as? [String: Any]
guard let lat = weather?["lat"] as? Double, let lon = weather?["lon"] as? Double else {
    result["status"] = "no_location"
    write(result, to: out)
    print("no_location")
    exit(0)
}
result["location"] = ["lat": lat, "lon": lon, "name": weather?["name"] as? String ?? ""]

do {
    let service = WeatherService.shared
    let (current, daily, hourly) = try await service.weather(
        for: CLLocation(latitude: lat, longitude: lon), including: .current, .daily, .hourly)
    result["status"] = "ok"
    result["current"] = [
        "condition": current.condition.rawValue,
        "description": current.condition.description,
        "symbol": current.symbolName,
        "temperature_c": celsius(current.temperature),
        "apparent_c": celsius(current.apparentTemperature),
        "humidity": (current.humidity * 100).rounded(),
        "wind_kph": (current.wind.speed.converted(to: .kilometersPerHour).value).rounded(),
    ]
    result["days"] = daily.forecast.prefix(3).map(day)
    let until = Date().addingTimeInterval(36 * 3600)
    result["hours"] = hourly.forecast.filter { $0.date >= Date().addingTimeInterval(-3600) && $0.date <= until }.map { h -> [String: Any] in
        [
            "time": iso.string(from: h.date),
            "condition": h.condition.rawValue,
            "temperature_c": celsius(h.temperature),
            // 服装の目安に使う体感温度（風・湿度込み）と、雨か雪か
            "apparent_c": celsius(h.apparentTemperature),
            "precipitation_chance": (h.precipitationChance * 100).rounded(),
            "precipitation": h.precipitation.rawValue,
            "wind_kph": (h.wind.speed.converted(to: .kilometersPerHour).value).rounded(),
        ]
    }
    let attribution = try await service.attribution
    result["attribution"] = [
        "service": attribution.serviceName,
        "legal_url": attribution.legalPageURL.absoluteString,
        "mark_light_url": attribution.combinedMarkLightURL.absoluteString,
        "mark_dark_url": attribution.combinedMarkDarkURL.absoluteString,
    ]
} catch {
    result["status"] = "error"
    result["error"] = "\(error)"
}
write(result, to: out)
print("\(result["status"]!)")
