// This Mac's side of the call's sound: the microphone through macOS voice processing (echo cancelling,
// so the owner can use speakers) to 16 kHz mono s16le in 20 ms frames, and the room played from a
// jitter buffer through the same engine, which is the echo canceller's reference.
import AVFoundation
import RexCallCore

final class CallAudio {
    let engine = AVAudioEngine()
    let room = JitterBuffer()
    private var source: AVAudioSourceNode?
    private var converter: AVAudioConverter?
    private var pending = Data()
    var muted = true
    var onVoice: (Data) -> Void = { _ in }
    private(set) var voiceProcessing = false
    private(set) var inputLevel: Float = 0   // Peak of the last frame sent, 0...1, for the talk meter.

    static let frameBytes = 640   // 20 ms at 16 kHz.

    func start() throws {
        let input = engine.inputNode
        do {
            try input.setVoiceProcessingEnabled(true)
            voiceProcessing = true
            if #available(macOS 14.0, *) {
                input.voiceProcessingOtherAudioDuckingConfiguration = .init(enableAdvancedDucking: false, duckingLevel: .min)
            }
        } catch {
            voiceProcessing = false   // Still works; headphones keep the room from hearing itself.
        }
        let roomFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(Wire.audioRate), channels: 1, interleaved: false)!
        let room = self.room
        let source = AVAudioSourceNode(format: roomFormat) { _, _, frames, buffers in
            let list = UnsafeMutableAudioBufferListPointer(buffers)
            if let data = list[0].mData {
                room.read(into: data.assumingMemoryBound(to: Float.self), count: Int(frames))
            }
            return noErr
        }
        self.source = source
        engine.attach(source)
        engine.connect(source, to: engine.mainMixerNode, format: roomFormat)

        let hardware = input.outputFormat(forBus: 0)
        guard hardware.sampleRate > 0, hardware.channelCount > 0 else { throw CallAudioError.noMicrophone }
        let mono = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: hardware.sampleRate, channels: 1, interleaved: false)!
        let wire = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: Double(Wire.audioRate), channels: 1, interleaved: true)!
        converter = AVAudioConverter(from: mono, to: wire)
        input.installTap(onBus: 0, bufferSize: 1024, format: hardware) { [weak self] buffer, _ in
            self?.captured(buffer, mono: mono, wire: wire)
        }
        engine.prepare()
        try engine.start()
    }

    private func captured(_ buffer: AVAudioPCMBuffer, mono: AVAudioFormat, wire: AVAudioFormat) {
        guard let converter, let channels = buffer.floatChannelData, buffer.frameLength > 0 else { return }
        // Channel 0 only: voice processing's output can carry extra channels.
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
        let bytes = Data(bytes: samples[0], count: Int(out.frameLength) * 2)
        if muted {
            pending.removeAll(keepingCapacity: true)
            inputLevel = 0
            return
        }
        pending.append(bytes)
        while pending.count >= Self.frameBytes {
            let frame = pending.prefix(Self.frameBytes)
            pending.removeFirst(Self.frameBytes)
            inputLevel = frame.withUnsafeBytes { raw in
                let values = raw.bindMemory(to: Int16.self)
                return Float(values.map { abs(Int32($0)) }.max() ?? 0) / 32768
            }
            onVoice(Data(frame))
        }
    }

    func stop() {
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        if let source { engine.detach(source) }
        room.clear()
    }
}

enum CallAudioError: Error { case noMicrophone }
