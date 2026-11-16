// A headless caller: signs in, listens and sends a test tone for a few seconds, hangs up and prints
// what it measured as JSON. For end-to-end checks against tools/telepresence_fake_robot.py (or a robot).
//
//   swift run rexcall-probe HOST:PORT [seconds]      token from TELEPRESENCE_TOKEN
import Foundation
import RexCallCore

let arguments = CommandLine.arguments
guard arguments.count >= 2, let token = ProcessInfo.processInfo.environment["TELEPRESENCE_TOKEN"] else {
    FileHandle.standardError.write(Data("usage: TELEPRESENCE_TOKEN=... rexcall-probe HOST:PORT [seconds]\n".utf8))
    exit(2)
}
let seconds = arguments.count > 2 ? Double(arguments[2]) ?? 5 : 5
let address = try Wire.parseAddress(arguments[1])
let link = RexLink(host: address.host, port: address.port, token: token, client: "rexcall-probe")
let lock = NSLock()
var welcome: [String: Any]?
var closed: String?
var audioFrames = 0, audioBytes = 0, videoFrames = 0
var ages: [Double] = []
var lastStatus: [String: Any] = [:]
let signedIn = DispatchSemaphore(value: 0)

link.onEvent = { event in
    lock.lock(); defer { lock.unlock() }
    switch event {
    case .welcome(let message): welcome = message; signedIn.signal()
    case .audio(let pcm): audioFrames += 1; audioBytes += pcm.count
    case .video(_, let age): videoFrames += 1; if let age { ages.append(age) }
    case .status(let message): lastStatus = message
    case .closed(let reason): closed = reason; signedIn.signal()
    }
}
link.start()
_ = signedIn.wait(timeout: .now() + 6)
guard welcome != nil else {
    print(#"{"outcome":"failed","reason":"\#(closed ?? "no_welcome")"}"#)
    exit(1)
}
// A 440 Hz tone at about -20 dBFS, 20 ms a frame, as the owner's voice.
var phase = 0.0
let started = Date()
while Date().timeIntervalSince(started) < seconds {
    lock.lock(); let ended = closed != nil; lock.unlock()
    if ended { break }
    var frame = Data(capacity: 640)
    for _ in 0..<320 {
        let sample = Int16(3277 * sin(phase))
        phase += 2 * .pi * 440 / 16000
        withUnsafeBytes(of: sample.littleEndian) { frame.append(contentsOf: $0) }
    }
    link.sendAudio(frame)
    Thread.sleep(forTimeInterval: 0.02)
}
let trips = link.roundTrips().sorted()
link.hangUp()
lock.lock()
let median = trips.isEmpty ? nil : trips[trips.count / 2] * 1000
let sortedAges = ages.sorted()
let report: [String: Any] = [
    "outcome": closed == nil || closed == "hung_up" ? "ok" : "ended_early",
    "closed": closed ?? "hung_up", "seconds": seconds,
    "room_audio_frames": audioFrames, "room_audio_seconds": Double(audioBytes) / 32000,
    "video_frames": videoFrames,
    "video_age_ms_median": sortedAges.isEmpty ? NSNull() : sortedAges[sortedAges.count / 2] * 1000,
    "round_trip_ms_median": median ?? NSNull(), "round_trips": trips.count,
    "voice_frames_sent": link.audioSent, "voice_frames_dropped": link.audioDropped,
    "robot_status": lastStatus,
]
lock.unlock()
let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
print(String(decoding: data, as: UTF8.self))
