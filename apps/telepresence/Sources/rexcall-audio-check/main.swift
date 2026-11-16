// Checks this Mac's sound the way a call uses it, with no robot: lists the devices, or starts the call
// audio on a chosen pair for a few seconds, plays a quiet tone as "the room" and reports what started.
//
//   rexcall-audio-check --list
//   rexcall-audio-check [--input NAME] [--output NAME] [--no-echo-cancelling] [--seconds 3] [--silent]
// --silent plays zeros instead of the tone: the speaker still has to keep up, but nothing is heard.
import Foundation
import RexCallAudio
import RexCallCore

var arguments = Array(CommandLine.arguments.dropFirst())
func option(_ name: String) -> String? {
    guard let i = arguments.firstIndex(of: name), i + 1 < arguments.count else { return nil }
    return arguments[i + 1]
}
func pick(_ name: String?, inputs: Bool) -> String? {
    guard let name else { return nil }
    let devices = inputs ? AudioDevices.inputs() : AudioDevices.outputs()
    guard let device = devices.first(where: { $0.name.localizedCaseInsensitiveContains(name) }) else {
        print("no \(inputs ? "input" : "output") device matching \(name)"); exit(2)
    }
    return device.uid
}

if arguments.contains("--list") {
    for device in AudioDevices.all() {
        print("\(device.name): \(device.inputs) in, \(device.outputs) out, \(Int(device.sampleRate)) Hz")
    }
    print("default input: \(AudioDevices.defaultDevice(input: true)?.name ?? "none"); default output: \(AudioDevices.defaultDevice(input: false)?.name ?? "none")")
    exit(0)
}
let choice = AudioChoice(inputUID: pick(option("--input"), inputs: true), outputUID: pick(option("--output"), inputs: false),
                         echoCancelling: !arguments.contains("--no-echo-cancelling"))
let seconds = Double(option("--seconds") ?? "3") ?? 3
let audio = CallAudio(choice)
var report: [String: Any] = ["asked_echo_cancelling": choice.echoCancelling]
do {
    try audio.start()
    report["started"] = true
    report["echo_cancelling"] = audio.voiceProcessing
    report["note"] = audio.note ?? NSNull()
    report["microphone"] = audio.inputName
    report["speaker"] = audio.outputName
    var peak: Float = 0
    var phase = 0.0
    let started = Date()
    while Date().timeIntervalSince(started) < seconds {
        var tone = Data(capacity: 640)   // --silent: zeros, which still prove the speaker pulls the room.
        let amplitude = arguments.contains("--silent") ? 0.0 : 1600.0
        for _ in 0..<320 {
            let sample = Int16(amplitude * sin(phase)); phase += 2 * .pi * 330 / 16000
            withUnsafeBytes(of: sample.littleEndian) { tone.append(contentsOf: $0) }
        }
        audio.room.write(tone)
        Thread.sleep(forTimeInterval: 0.02)
        peak = max(peak, audio.heardLevel)
    }
    report["microphone_peak"] = Double(peak)
    report["room_underruns"] = audio.room.underruns
    report["room_dropped_seconds"] = audio.room.droppedSeconds   // Near 0 when the speaker plays it as it comes.
    report["room_buffered_seconds"] = audio.room.bufferedSeconds
    audio.stop()
} catch {
    report["started"] = false
    report["error"] = "\(error)"
}
let data = try JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys])
print(String(decoding: data, as: UTF8.self))
