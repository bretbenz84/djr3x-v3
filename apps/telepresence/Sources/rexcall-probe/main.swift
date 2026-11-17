// A headless caller: signs in, listens and sends a test tone for a few seconds, hangs up and prints
// what it measured as JSON. For end-to-end checks against tools/telepresence_fake_robot.py (or a robot).
//
//   rexcall-probe HOST:PORT [seconds] [--start] [--steer]     token from TELEPRESENCE_TOKEN
//   rexcall-probe HOST:PORT --trigger status|start|stop
// --start asks the supervisor to start the call first. --steer aims the head, drives forward for
// 1.5 s, lets go, then presses Stop, and reports the robot's head_state and drive_state answers.
import Foundation
import RexCallCore

var arguments = CommandLine.arguments
func flag(_ name: String) -> Bool { arguments.contains(name) }
func option(_ name: String) -> String? {
    guard let i = arguments.firstIndex(of: name), i + 1 < arguments.count else { return nil }
    return arguments[i + 1]
}
func emit(_ object: [String: Any]) {
    let data = try! JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys])
    print(String(decoding: data, as: UTF8.self))
}
guard arguments.count >= 2, let token = ProcessInfo.processInfo.environment["TELEPRESENCE_TOKEN"] else {
    FileHandle.standardError.write(Data("usage: TELEPRESENCE_TOKEN=... rexcall-probe HOST:PORT [seconds] [--start] [--steer]\n".utf8))
    exit(2)
}
let address = try Wire.parseAddress(arguments[1])

func ask(_ type: String) -> [String: Any] {
    let done = DispatchSemaphore(value: 0)
    var answer: [String: Any] = [:]
    Trigger.ask(host: address.host, callPort: address.port, token: token, ["type": type]) { result in
        switch result {
        case .success(let reply): answer = reply
        case .failure(let error): answer = ["type": "error", "reason": "\(error)"]
        }
        done.signal()
    }
    done.wait()
    return answer
}

if let action = option("--trigger") {
    emit(ask(action))
    exit(0)
}
var startAnswer: [String: Any]?
if flag("--start") { startAnswer = ask("start") }

let seconds = arguments.count > 2 ? Double(arguments[2]) ?? 5 : 5
let lock = NSLock()
var welcome: [String: Any]?
var closed: String?
var audioFrames = 0, audioBytes = 0, videoFrames = 0
var ages: [Double] = []
var lastStatus: [String: Any] = [:]
var headStates: [[String: Any]] = []
var driveStates: [[String: Any]] = []
let signedIn = DispatchSemaphore(value: 0)

// The robot may still be waking after a start: keep calling for up to 60 s.
var link: RexLink!
let callDeadline = Date().addingTimeInterval(60)
while true {
    link = RexLink(host: address.host, port: address.port, token: token, client: "rexcall-probe")
    link.onEvent = { event in
        lock.lock(); defer { lock.unlock() }
        switch event {
        case .welcome(let message): welcome = message; signedIn.signal()
        case .audio(let pcm): audioFrames += 1; audioBytes += pcm.count
        case .video(_, let age): videoFrames += 1; if let age { ages.append(age) }
        case .status(let message): lastStatus = message
        case .message(let message):
            if message["type"] as? String == "head_state" { headStates.append(message) }
            if message["type"] as? String == "drive_state" { driveStates.append(message) }
        case .closed(let reason): closed = reason; signedIn.signal()
        }
    }
    link.start()
    _ = signedIn.wait(timeout: .now() + 6)
    lock.lock(); let ok = welcome != nil; let why = closed; lock.unlock()
    if ok { break }
    if Date() > callDeadline || !["connection_refused", "timed_out", "unreachable"].contains(why ?? "") {
        emit(["outcome": "failed", "reason": why ?? "no_welcome", "start": startAnswer ?? NSNull()])
        exit(1)
    }
    lock.lock(); closed = nil; lock.unlock()
    Thread.sleep(forTimeInterval: 2)
}

func send(for duration: Double, lin: Double = 0, ang: Double = 0, tone phase: inout Double) {
    let until = Date().addingTimeInterval(duration)
    var tick = 0
    while Date() < until {
        lock.lock(); let ended = closed != nil; lock.unlock()
        if ended { return }
        var frame = Data(capacity: 640)
        for _ in 0..<320 {
            let sample = Int16(3277 * sin(phase))
            phase += 2 * .pi * 440 / 16000
            withUnsafeBytes(of: sample.littleEndian) { frame.append(contentsOf: $0) }
        }
        link.sendAudio(frame)
        if tick % 5 == 0 && (lin != 0 || ang != 0 || flag("--steer")) { link.sendDrive(lin: lin, ang: ang) }
        tick += 1
        Thread.sleep(forTimeInterval: 0.02)
    }
}

var phase = 0.0
let controls = welcome?["controls"] as? [String: Any]
if flag("--steer") {
    send(for: 1.5, tone: &phase)                                  // A round trip is measured first.
    link.sendHead(["neck": 0.5, "pitch": -0.2])
    send(for: 2.0, tone: &phase)
    send(for: 1.5, lin: 0.5, tone: &phase)                        // Forward at half the cap.
    send(for: 0.6, tone: &phase)                                  // Let go: zeros.
    link.sendHead(["neck": -0.5])                                 // Should be allowed again once still.
    send(for: 1.5, tone: &phase)
    send(for: 0.5, lin: 0.4, tone: &phase)
    link.sendStop()                                               // Space bar.
    send(for: 0.5, lin: 0.4, tone: &phase)                        // Held: the stick has not been released.
    send(for: 0.5, tone: &phase)                                  // Released.
    send(for: max(0, seconds - 9.1), tone: &phase)
} else {
    send(for: seconds, tone: &phase)
}
let trips = link.roundTrips().sorted()
link.hangUp()
lock.lock()
let sortedAges = ages.sorted()
emit([
    "outcome": closed == nil || closed == "hung_up" ? "ok" : "ended_early",
    "closed": closed ?? "hung_up", "seconds": seconds, "start": startAnswer ?? NSNull(),
    "controls_offered": controls ?? NSNull(),
    "room_audio_frames": audioFrames, "room_audio_seconds": Double(audioBytes) / 32000,
    "video_frames": videoFrames,
    "video_age_ms_median": sortedAges.isEmpty ? NSNull() : sortedAges[sortedAges.count / 2] * 1000,
    "round_trip_ms_median": trips.isEmpty ? NSNull() : trips[trips.count / 2] * 1000, "round_trips": trips.count,
    "voice_frames_sent": link.audioSent, "voice_frames_dropped": link.audioDropped,
    "head_states": headStates, "drive_states": driveStates,
    "robot_status_head": lastStatus["head"] ?? NSNull(), "robot_status_drive": lastStatus["drive"] ?? NSNull(),
    "robot_status_base": lastStatus["base"] ?? NSNull(),
])
lock.unlock()
