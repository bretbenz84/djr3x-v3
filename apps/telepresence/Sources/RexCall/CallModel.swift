// One call at a time: connect (retrying while the robot wakes), then room sound and video in, the
// owner's voice out while unmuted, and a hang-up that tells the robot to park.
import AppKit
import AVFoundation
import ImageIO
import RexCallCore

@MainActor
final class CallModel: ObservableObject {
    enum Phase: Equatable { case idle, connecting(String), inCall, ended(String) }

    static let wakeRetrySeconds = 60.0   // The robot may still be waking when the app first calls.

    @Published var phase: Phase = .idle
    @Published var address: String = UserDefaults.standard.string(forKey: "address") ?? ""
    @Published var hasToken = Keychain.token() != nil
    @Published var image: CGImage?
    @Published var videoExpected = false
    @Published var videoAgeMs: Double?
    @Published var fps: Double = 0
    @Published var rttMs: Double?
    @Published var muted = true { didSet { audio?.muted = muted } }
    @Published var level: Float = 0
    @Published var voiceProcessing = false
    @Published var robotLine = ""

    private var link: RexLink?
    private var audio: CallAudio?
    private var timer: Timer?
    private var framesThisSecond = 0
    private var attemptStarted = Date()
    private var ages: [Double] = []

    func saveToken(_ token: String) -> Bool {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 32, !trimmed.contains(" ") else { return false }
        hasToken = Keychain.save(trimmed)
        return hasToken
    }

    func connect() {
        guard let token = Keychain.token() else { phase = .ended("no_token"); return }
        guard (try? Wire.parseAddress(address)) != nil else { phase = .ended("bad_address"); return }
        UserDefaults.standard.set(address, forKey: "address")
        attemptStarted = Date()
        Task {
            guard await Self.microphoneAllowed() else { phase = .ended("microphone_denied"); return }
            attempt(token: token)
        }
    }

    private static func microphoneAllowed() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .audio)
        default: return false
        }
    }

    private func attempt(token: String) {
        guard let (host, port) = try? Wire.parseAddress(address) else { return }
        phase = .connecting(Date().timeIntervalSince(attemptStarted) < 2 ? "Calling Rex…" : "Waiting for Rex to wake…")
        let link = RexLink(host: host, port: port, token: token, client: "rex-call-mac")
        self.link = link
        link.onEvent = { [weak self] event in
            // On the link's queue: decode pictures here, then hand results to the main actor.
            switch event {
            case .audio(let pcm):
                self?.audio?.room.write(pcm)   // The jitter buffer has its own lock.
            case .video(let jpeg, let age):
                guard let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
                      let picture = CGImageSourceCreateImageAtIndex(source, 0, nil) else { return }
                Task { @MainActor in self?.showFrame(picture, age: age) }
            default:
                Task { @MainActor in self?.handle(event, link: link, token: token) }
            }
        }
        link.start()
    }

    private func handle(_ event: RexLink.Event, link: RexLink, token: String) {
        guard link === self.link else { return }
        switch event {
        case .welcome(let message):
            videoExpected = message["video"] is [String: Any]
            startCall(link)
        case .status(let message):
            if let speaker = message["speaker"] as? [String: Any] {
                robotLine = "Rex's speaker: \(speaker["buffered_ms"] ?? "–") ms queued, \(speaker["underruns"] ?? 0) gaps"
            }
        case .closed(let reason):
            let retryable = ["connection_refused", "timed_out", "unreachable", "closed_during_sign_in"].contains(reason)
            if case .connecting = phase, retryable, Date().timeIntervalSince(attemptStarted) < Self.wakeRetrySeconds {
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                    guard let self, case .connecting = self.phase else { return }
                    self.attempt(token: token)
                }
                phase = .connecting("Waiting for Rex to wake…")
                return
            }
            endCall(reason)
        default:
            break
        }
    }

    private func startCall(_ link: RexLink) {
        let audio = CallAudio()
        audio.muted = true
        muted = true
        audio.onVoice = { [weak link] frame in link?.sendAudio(frame) }
        do {
            try audio.start()
        } catch {
            link.hangUp()
            endCall("audio_failed")
            return
        }
        self.audio = audio
        voiceProcessing = audio.voiceProcessing
        phase = .inCall
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
    }

    private func showFrame(_ picture: CGImage, age: Double?) {
        guard phase == .inCall else { return }
        image = picture
        framesThisSecond += 1
        if let age { ages.append(age); if ages.count > 15 { ages.removeFirst(ages.count - 15) } }
        videoAgeMs = ages.isEmpty ? nil : ages.sorted()[ages.count / 2] * 1000
    }

    private func tick() {
        fps = Double(framesThisSecond)
        framesThisSecond = 0
        level = audio?.inputLevel ?? 0
        if let link { rttMs = link.snapshot().rttMs }
    }

    func toggleMute() { muted.toggle() }

    func hangUp() {
        guard let link else { return }
        self.link = nil
        DispatchQueue.global().async { link.hangUp() }   // Blocks up to a second for the robot to hear it.
        endCall("hung_up")
    }

    /// At quit: the robot must still hear the hang-up.
    func hangUpBeforeQuit() {
        guard let link else { return }
        self.link = nil
        link.hangUp()
        audio?.stop()
    }

    private func endCall(_ reason: String) {
        timer?.invalidate(); timer = nil
        audio?.stop(); audio = nil
        link = nil
        image = nil; videoAgeMs = nil; rttMs = nil; fps = 0; level = 0; ages.removeAll(); robotLine = ""
        muted = true
        phase = .ended(reason)
    }

    static func explain(_ reason: String) -> String {
        switch reason {
        case "hung_up": return "Call ended."
        case "wrong_token": return "Rex refused the token. Paste the telepresence_token line from the robot's local/secrets.toml."
        case "busy": return "Someone else is already on a call with Rex."
        case "connection_refused", "closed_during_sign_in": return "Rex isn't taking calls. Is telepresence running on the robot?"
        case "unreachable", "timed_out": return "Can't reach Rex. Is the VPN connected, and is the address right?"
        case "robot_closed": return "The call ended at Rex's end."
        case "robot_silent": return "Lost the connection to Rex."
        case "microphone_denied": return "This Mac's microphone is off for Rex Call. Allow it in System Settings → Privacy & Security → Microphone."
        case "audio_failed": return "This Mac's sound could not start."
        case "no_token": return "Paste the telepresence token first."
        case "bad_address": return "The address needs to look like 192.168.1.50:47800."
        default: return "The call ended (\(reason))."
        }
    }
}
