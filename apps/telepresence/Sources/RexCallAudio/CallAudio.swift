// This Mac's side of the call's sound: the microphone as 16 kHz mono s16le in 20 ms frames, and the
// room played from a jitter buffer.
//
// Two ways, both measured on the dev Mac on November 16:
// - Echo cancelling (macOS voice processing), so the owner can use speakers. macOS builds it from the
//   system's default microphone and speaker and ignores a device the app picks, so it is offered only
//   with both on "System default". Its two client-side formats must share a rate: the mixer is wired
//   to the speaker side in the format voice processing gives it, read before the mixer is first
//   touched (touching it wires itself in at 44.1 kHz, and start then fails with -10875, which is what
//   "couldn't start this Mac's sound" was). If it still will not start, the plain way is used.
// - Plain, for any microphone and speaker: a speaker-only engine on the chosen output and a capture
//   session on the chosen microphone that delivers 16 kHz mono s16 itself. No echo cancelling.
import AVFoundation
import AudioToolbox
import CoreAudio
import RexCallCore

public struct AudioChoice: Equatable, Sendable {
    public var inputUID: String?      // nil: the system's default
    public var outputUID: String?
    public var echoCancelling: Bool

    public init(inputUID: String? = nil, outputUID: String? = nil, echoCancelling: Bool = true) {
        self.inputUID = inputUID; self.outputUID = outputUID; self.echoCancelling = echoCancelling
    }

    /// Echo cancelling only works on the system's default devices.
    public var canCancelEcho: Bool { inputUID == nil && outputUID == nil }
}

public enum CallAudioError: Error, CustomStringConvertible {
    case deviceMissing(String)
    case deviceRefused(String, Int)
    case engine(String)

    public var description: String {
        switch self {
        case .deviceMissing(let which): return "the chosen \(which) is not connected"
        case .deviceRefused(let which, let code): return "the \(which) could not be used (\(code))"
        case .engine(let detail): return detail
        }
    }
}

public final class CallAudio: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate {
    public static let frameBytes = 640   // 20 ms at 16 kHz.

    public let room = JitterBuffer()
    public let choice: AudioChoice
    public var muted = true
    public var onVoice: (Data) -> Void = { _ in }
    public private(set) var voiceProcessing = false
    public private(set) var note: String?          // Why echo cancelling is off when it was asked for.
    public private(set) var inputLevel: Float = 0  // Peak of the last frame sent, 0...1.
    public private(set) var heardLevel: Float = 0  // Peak heard, muted or not (the device check).
    public private(set) var inputName = ""
    public private(set) var outputName = ""

    private var engine: AVAudioEngine?             // Voice processing, or the plain way's speaker.
    private var session: AVCaptureSession?         // The plain way's microphone.
    private let captureQueue = DispatchQueue(label: "rexcall.microphone")
    private var converter: AVAudioConverter?
    private var pending = Data()
    private var tapInstalled = false

    public init(_ choice: AudioChoice) { self.choice = choice }

    public func start() throws {
        if choice.echoCancelling && choice.canCancelEcho {
            do {
                try startVoiceProcessing()
                return
            } catch {
                note = "Echo cancelling could not start (\(error)), so it is off: use headphones."
                stopDevices()
            }
        }
        if choice.echoCancelling && !choice.canCancelEcho {
            note = "Echo cancelling needs the system default microphone and speaker, so it is off: use headphones."
        }
        try startPlainSpeaker()
        try startPlainMicrophone()
    }

    // MARK: Echo cancelling

    private func startVoiceProcessing() throws {
        let engine = AVAudioEngine()
        self.engine = engine
        let input = engine.inputNode
        let output = engine.outputNode
        do { try input.setVoiceProcessingEnabled(true) } catch {
            throw CallAudioError.engine("voice processing refused (\((error as NSError).code))")
        }
        input.voiceProcessingOtherAudioDuckingConfiguration = .init(enableAdvancedDucking: false, duckingLevel: .min)
        let micFormat = input.outputFormat(forBus: 0)
        let speakerFormat = output.inputFormat(forBus: 0)   // Before mainMixerNode is touched.
        guard micFormat.sampleRate > 0, micFormat.channelCount > 0 else { throw CallAudioError.engine("no microphone input") }
        attachRoom(to: engine)
        engine.connect(engine.mainMixerNode, to: output, format: speakerFormat)
        let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: micFormat.sampleRate, channels: 1, interleaved: false)!
        let wire = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(Wire.audioRate), channels: 1, interleaved: true)!
        converter = AVAudioConverter(from: mono, to: wire)
        input.installTap(onBus: 0, bufferSize: 1024, format: micFormat) { [weak self] buffer, _ in
            self?.tapped(buffer, mono: mono, wire: wire)
        }
        tapInstalled = true
        engine.prepare()
        do { try engine.start() } catch {
            throw CallAudioError.engine("the sound engine would not start (\((error as NSError).code))")
        }
        voiceProcessing = true
        inputName = AudioDevices.defaultDevice(input: true)?.name ?? "system default"
        outputName = AudioDevices.defaultDevice(input: false)?.name ?? "system default"
    }

    private func tapped(_ buffer: AVAudioPCMBuffer, mono: AVAudioFormat, wire: AVAudioFormat) {
        guard let converter, let channels = buffer.floatChannelData, buffer.frameLength > 0 else { return }
        // Channel 0 only: voice processing's microphone side carries extra channels.
        guard let first = AVAudioPCMBuffer(pcmFormat: mono, frameCapacity: buffer.frameLength) else { return }
        first.frameLength = buffer.frameLength
        first.floatChannelData![0].update(from: channels[0], count: Int(buffer.frameLength))
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * wire.sampleRate / mono.sampleRate) + 32
        guard let out = AVAudioPCMBuffer(pcmFormat: wire, frameCapacity: capacity) else { return }
        var given = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if given { status.pointee = .noDataNow; return nil }
            given = true
            status.pointee = .haveData
            return first
        }
        guard error == nil, out.frameLength > 0, let samples = out.int16ChannelData else { return }
        heard(Data(bytes: samples[0], count: Int(out.frameLength) * 2))
    }

    // MARK: Plain

    private func startPlainSpeaker() throws {
        let engine = AVAudioEngine()   // Its input node is never touched: an output-only device is accepted.
        self.engine = engine
        if let uid = choice.outputUID {
            guard let device = AudioDevices.device(uid: uid), device.outputs > 0 else { throw CallAudioError.deviceMissing("speaker") }
            guard let unit = engine.outputNode.audioUnit else { throw CallAudioError.deviceRefused("speaker", -1) }
            var id = device.id
            let status = AudioUnitSetProperty(unit, kAudioOutputUnitProperty_CurrentDevice, kAudioUnitScope_Global, 0,
                                              &id, UInt32(MemoryLayout<AudioDeviceID>.size))
            if status != noErr { throw CallAudioError.deviceRefused("speaker", Int(status)) }
            outputName = device.name
        } else {
            outputName = AudioDevices.defaultDevice(input: false)?.name ?? "system default"
        }
        attachRoom(to: engine)
        engine.prepare()
        do { try engine.start() } catch {
            throw CallAudioError.engine("the speaker would not start (\((error as NSError).code))")
        }
    }

    private func startPlainMicrophone() throws {
        let device: AVCaptureDevice?
        if let uid = choice.inputUID {
            device = AVCaptureDevice(uniqueID: uid)
            if device == nil { throw CallAudioError.deviceMissing("microphone") }
        } else {
            device = AVCaptureDevice.default(for: .audio)
        }
        guard let device else { throw CallAudioError.deviceMissing("microphone") }
        let session = AVCaptureSession()
        do {
            let input = try AVCaptureDeviceInput(device: device)
            guard session.canAddInput(input) else { throw CallAudioError.deviceRefused("microphone", -2) }
            session.addInput(input)
        } catch let error as CallAudioError {
            throw error
        } catch {
            throw CallAudioError.deviceRefused("microphone", (error as NSError).code)
        }
        let output = AVCaptureAudioDataOutput()
        output.audioSettings = [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: Wire.audioRate, AVNumberOfChannelsKey: 1,
                                AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false,
                                AVLinearPCMIsNonInterleaved: false, AVLinearPCMIsBigEndianKey: false]
        output.setSampleBufferDelegate(self, queue: captureQueue)
        guard session.canAddOutput(output) else { throw CallAudioError.deviceRefused("microphone", -3) }
        session.addOutput(output)
        session.startRunning()
        self.session = session
        inputName = device.localizedName
    }

    public func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let block = CMSampleBufferGetDataBuffer(sampleBuffer) else { return }
        let length = CMBlockBufferGetDataLength(block)
        var data = Data(count: length)
        let status = data.withUnsafeMutableBytes { CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: length, destination: $0.baseAddress!) }
        if status == kCMBlockBufferNoErr { heard(data) }
    }

    // MARK: Shared

    private func attachRoom(to engine: AVAudioEngine) {
        let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(Wire.audioRate), channels: 1, interleaved: false)!
        let room = self.room
        let source = AVAudioSourceNode(format: format) { _, _, frames, buffers in
            for buffer in UnsafeMutableAudioBufferListPointer(buffers) {
                guard let data = buffer.mData else { continue }
                room.read(into: data.assumingMemoryBound(to: Float.self), count: Int(frames))
            }
            return noErr
        }
        engine.attach(source)
        engine.connect(source, to: engine.mainMixerNode, format: format)
    }

    /// 16 kHz mono s16le from either microphone path: framed, measured, sent while unmuted.
    private func heard(_ pcm: Data) {
        heardLevel = pcm.withUnsafeBytes { raw in
            Float(raw.bindMemory(to: Int16.self).map { abs(Int32($0)) }.max() ?? 0) / 32768
        }
        if muted {
            pending.removeAll(keepingCapacity: true)
            inputLevel = 0
            return
        }
        pending.append(pcm)
        while pending.count >= Self.frameBytes {
            let frame = Data(pending.prefix(Self.frameBytes))
            pending.removeFirst(Self.frameBytes)
            inputLevel = frame.withUnsafeBytes { raw in
                Float(raw.bindMemory(to: Int16.self).map { abs(Int32($0)) }.max() ?? 0) / 32768
            }
            onVoice(frame)
        }
    }

    private func stopDevices() {
        session?.stopRunning()
        session = nil
        if let engine {
            if tapInstalled { engine.inputNode.removeTap(onBus: 0); tapInstalled = false }
            engine.stop()
        }
        engine = nil
        converter = nil
        voiceProcessing = false
    }

    public func stop() {
        stopDevices()
        room.clear()
    }
}
