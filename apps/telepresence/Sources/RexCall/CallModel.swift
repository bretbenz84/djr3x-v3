// One call at a time. Connecting asks Rex's supervisor first (stage 4): a call starts only while he is
// asleep, never over a conversation. Then room sound and video in, the owner's voice out while
// unmuted, and, when the robot offers them, the head and the wheels (stages 3 and 5). Hang-up parks him.
import AppKit
import AVFoundation
import GameController
import ImageIO
import RexCallAudio
import RexCallCore

@MainActor
final class CallModel: ObservableObject {
    enum Phase: Equatable { case idle, connecting(String), inCall, ended(String) }

    static let wakeRetrySeconds = 60.0   // The robot may still be waking when the app first calls.
    static let pictureLateSeconds = 0.5  // An older picture holds the wheels (the plan's rule).
    static let controlHz = 10.0

    // Call
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
    @Published var supervisorLine = ""
    @Published var supervisorState: String?

    // Sound devices
    @Published var inputs: [AudioDevice] = []
    @Published var outputs: [AudioDevice] = []
    @Published var audioChoice = CallModel.savedChoice() { didSet { saveChoice(); if audioChoice != oldValue { restartAudio() } } }
    @Published var audioNote: String?
    @Published var audioInUse = ""

    // Head and wheels
    @Published var headOffered = false
    @Published var driveOffered = false
    @Published var aim = HeadAim()
    @Published var headNote: String?
    @Published var driveLine = ""
    @Published var baseLine = ""
    @Published var holdLine: String?          // Why the app itself is holding the wheels.
    @Published var speed: Double = UserDefaults.standard.object(forKey: "speed") as? Double ?? 0.5 {
        didSet { UserDefaults.standard.set(speed, forKey: "speed") }
    }
    @Published var maxSpeed = "" // "0.26 m/s, 60°/s" from the welcome.
    @Published var controllerName: String?
    var steering = Steering()
    private var home = HeadAim()
    private var sentAim: HeadAim?
    private var headKeys: Set<String> = []     // Held: neck-, neck+, pitch-, pitch+, tilt-, tilt+, lift-, lift+
    private var pacer = SetpointPacer()
    private var controlTimer: Timer?
    private var lastFrameAt: Date?
    private var pushToTalk = false
    private var padButtons: [String: Bool] = [:]

    private var link: RexLink?
    private var audio: CallAudio?
    private var timer: Timer?
    private var framesThisSecond = 0
    private var attemptStarted = Date()
    private var ages: [Double] = []
    private var startedBySupervisor = false
    private var supervisorTimer: Timer?

    // MARK: Sound devices

    static func savedChoice() -> AudioChoice {
        let defaults = UserDefaults.standard
        return AudioChoice(inputUID: defaults.string(forKey: "inputUID"), outputUID: defaults.string(forKey: "outputUID"),
                           echoCancelling: defaults.object(forKey: "echoCancelling") as? Bool ?? true)
    }

    private func saveChoice() {
        let defaults = UserDefaults.standard
        defaults.set(audioChoice.inputUID, forKey: "inputUID")
        defaults.set(audioChoice.outputUID, forKey: "outputUID")
        defaults.set(audioChoice.echoCancelling, forKey: "echoCancelling")
    }

    func refreshDevices() {
        inputs = AudioDevices.inputs()
        outputs = AudioDevices.outputs()
        // A device that is gone falls back to the system default rather than failing the next call.
        if let uid = audioChoice.inputUID, !inputs.contains(where: { $0.uid == uid }) { audioChoice.inputUID = nil }
        if let uid = audioChoice.outputUID, !outputs.contains(where: { $0.uid == uid }) { audioChoice.outputUID = nil }
    }

    func defaultName(input: Bool) -> String { AudioDevices.defaultDevice(input: input)?.name ?? "none" }

    /// A new choice during a call takes effect at once; the robot keeps the call.
    private func restartAudio() {
        guard phase == .inCall, let link, let old = audio else { return }
        old.stop()
        audio = nil
        startAudio(for: link)
    }

    @discardableResult
    private func startAudio(for link: RexLink) -> Bool {
        let audio = CallAudio(audioChoice)
        audio.muted = muted
        audio.onVoice = { [weak link] frame in link?.sendAudio(frame) }
        do {
            try audio.start()
        } catch {
            audioNote = "This Mac's sound could not start: \(error)."
            audioInUse = ""
            return false
        }
        self.audio = audio
        voiceProcessing = audio.voiceProcessing
        audioNote = audio.note
        audioInUse = "\(audio.inputName) → Rex, Rex → \(audio.outputName)"
        return true
    }

    func saveToken(_ token: String) -> Bool {
        let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
        guard trimmed.count >= 32, !trimmed.contains(" ") else { return false }
        hasToken = Keychain.save(trimmed)
        return hasToken
    }

    // MARK: The supervisor

    /// What the supervisor says about Rex, for the connect screen; quiet when there is none.
    func refreshSupervisor() {
        guard let token = Keychain.token(), let (host, port) = try? Wire.parseAddress(address) else {
            supervisorLine = ""; supervisorState = nil; return
        }
        Trigger.ask(host: host, callPort: port, token: token, ["type": "status"]) { result in
            Task { @MainActor [weak self] in self?.showSupervisor(result) }
        }
    }

    func watchSupervisor(_ on: Bool) {
        supervisorTimer?.invalidate(); supervisorTimer = nil
        guard on else { return }
        refreshSupervisor()
        supervisorTimer = Timer.scheduledTimer(withTimeInterval: 15, repeats: true) { _ in
            Task { @MainActor [weak self] in self?.refreshSupervisor() }
        }
    }

    private func showSupervisor(_ result: Result<[String: Any], TriggerError>) {
        switch result {
        case .success(let status):
            let state = status["state"] as? String
            supervisorState = state
            var parts = [state == "asleep" ? "Rex is asleep: a call will wake him."
                         : state == "telepresence" ? "Rex is on a call now."
                         : state == "awake" ? "Rex is awake (talking, playing or on guard): calls wait until he sleeps."
                         : "Rex's supervisor answered."]
            if let battery = status["battery"] as? [String: Any], let percent = battery["percent"] {
                parts.append("Battery \(percent)%\((battery["charging"] as? Bool) == true ? ", charging" : "").")
            }
            if let last = status["last_call"] as? [String: Any], let reason = last["reason"] as? String {
                // failed_starts: [{capability, reason}] for parts that did not start that call.
                let failed = (last["failed_starts"] as? [[String: Any]] ?? []).compactMap { $0["capability"] as? String }
                parts.append("Last call: \(reason)" + (failed.isEmpty ? "" : " (did not start: \(failed.joined(separator: ", ")))") + ".")
            }
            supervisorLine = parts.joined(separator: " ")
        case .failure(.refused(let why)):
            supervisorState = nil
            supervisorLine = why == "wrong_token" ? "Rex's supervisor refused the token." : "Rex's supervisor refused (\(why))."
        case .failure:
            supervisorState = nil
            supervisorLine = "No supervisor answered. A call started on the robot by hand still works."
        }
    }

    /// End a call the supervisor started, from the connect screen (one left running elsewhere).
    func endRunningCall() {
        guard let token = Keychain.token(), let (host, port) = try? Wire.parseAddress(address) else { return }
        Trigger.ask(host: host, callPort: port, token: token, ["type": "stop"]) { _ in
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                self?.refreshSupervisor()
            }
        }
    }

    // MARK: Calling

    func connect() {
        guard let token = Keychain.token() else { phase = .ended("no_token"); return }
        guard let (host, port) = try? Wire.parseAddress(address) else { phase = .ended("bad_address"); return }
        UserDefaults.standard.set(address, forKey: "address")
        watchSupervisor(false)
        startedBySupervisor = false
        phase = .connecting("Asking Rex's supervisor…")
        Task {
            guard await Self.microphoneAllowed() else { phase = .ended("microphone_denied"); return }
            Trigger.ask(host: host, callPort: port, token: token, ["type": "status"]) { result in
                Task { @MainActor [weak self] in self?.afterStatus(result, token: token, host: host, port: port) }
            }
        }
    }

    private func afterStatus(_ result: Result<[String: Any], TriggerError>, token: String, host: String, port: UInt16) {
        guard case .connecting = phase else { return }   // Cancelled meanwhile.
        switch result {
        case .success(let status):
            showSupervisor(result)
            switch status["state"] as? String {
            case "awake":
                endCall("rex_awake")
            case "telepresence":
                callNow(token: token)
            default:
                phase = .connecting("Waking Rex…")
                Trigger.ask(host: host, callPort: port, token: token, ["type": "start"]) { result in
                    Task { @MainActor [weak self] in self?.afterStart(result, token: token) }
                }
            }
        case .failure(.refused(let why)):
            endCall(why)
        case .failure:
            callNow(token: token)   // No supervisor: a call started by hand, or an older robot.
        }
    }

    private func afterStart(_ result: Result<[String: Any], TriggerError>, token: String) {
        guard case .connecting = phase else { return }
        switch result {
        case .success(let answer) where answer["type"] as? String == "starting":
            startedBySupervisor = !(answer["already_running"] as? Bool ?? false)
            callNow(token: token)
        case .success(let answer):
            endCall(answer["reason"] as? String ?? "start_refused")
        case .failure(.refused(let why)):
            endCall(why)
        case .failure:
            callNow(token: token)
        }
    }

    private func callNow(token: String) {
        attemptStarted = Date()
        attempt(token: token)
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
        if case .connecting = phase {
            phase = .connecting(startedBySupervisor || Date().timeIntervalSince(attemptStarted) > 2 ? "Waiting for Rex to wake…" : "Calling Rex…")
        }
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
            offerControls(message["controls"] as? [String: Any])
            startCall(link)
        case .status(let message):
            showStatus(message)
        case .message(let message):
            switch message["type"] as? String {
            case "head_state":
                let status = message["status"] as? String
                headNote = status == "pulses_reached" ? nil : Self.explainHead(message["reason"] as? String ?? status ?? "")
            case "drive_state":
                driveLine = Self.describeWheels(message)
            default: break
            }
        case .closed(let reason):
            let retryable = ["connection_refused", "timed_out", "unreachable", "closed_during_sign_in"].contains(reason)
            if case .connecting = phase, retryable, Date().timeIntervalSince(attemptStarted) < Self.wakeRetrySeconds {
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in
                    guard let self, case .connecting = self.phase else { return }
                    self.attempt(token: token)
                }
                return
            }
            if case .connecting = phase, startedBySupervisor {
                endCall("call_never_answered")   // The supervisor's status says why (last call).
                refreshSupervisor()
                return
            }
            endCall(reason)
        default:
            break
        }
    }

    private func offerControls(_ controls: [String: Any]?) {
        let head = controls?["head"] as? [String: Any]
        headOffered = head != nil
        home = HeadAim(home: head?["home"] as? [String: Any])
        aim = home
        sentAim = home    // The robot starts there: nothing to send until the caller moves it.
        let drive = controls?["drive"] as? [String: Any]
        driveOffered = drive != nil
        if let drive, let lin = drive["max_lin_mps"] as? Double, let turn = drive["max_turn_dps"] {
            maxSpeed = "\(String(format: "%.2f", lin)) m/s, \(turn)°/s"
        }
        driveLine = driveOffered ? "Wheels ready." : ""
        headNote = nil
    }

    private func startCall(_ link: RexLink) {
        muted = true
        guard startAudio(for: link) else {
            let detail = audioNote ?? "This Mac's sound could not start."
            DispatchQueue.global().async { link.hangUp() }
            self.link = nil
            endCall("audio_failed")
            audioNote = detail
            return
        }
        phase = .inCall
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.tick() }
        }
        controlTimer = Timer.scheduledTimer(withTimeInterval: 1 / Self.controlHz, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.controlTick() }
        }
    }

    private func showFrame(_ picture: CGImage, age: Double?) {
        guard phase == .inCall else { return }
        image = picture
        lastFrameAt = Date()
        framesThisSecond += 1
        if let age { ages.append(age); if ages.count > 15 { ages.removeFirst(ages.count - 15) } }
        videoAgeMs = ages.isEmpty ? nil : ages.sorted()[ages.count / 2] * 1000
    }

    private func showStatus(_ message: [String: Any]) {
        if let speaker = message["speaker"] as? [String: Any] {
            robotLine = "Rex's speaker \(speaker["buffered_ms"] ?? "–") ms queued, \(speaker["underruns"] ?? 0) gaps"
        }
        if let drive = message["drive"] as? [String: Any] { driveLine = Self.describeWheels(drive) }
        if let head = message["head"] as? [String: Any] { followHead(head) }
        if let base = message["base"] as? [String: Any] { baseLine = Self.describeBase(base) }
    }

    private func tick() {
        fps = Double(framesThisSecond)
        framesThisSecond = 0
        level = audio?.inputLevel ?? 0
        if let link { rttMs = link.snapshot().rttMs }
        controllerName = GCController.current?.vendorName
    }

    func toggleMute() { muted.toggle() }

    func hangUp() {
        guard let link else { endCall("hung_up"); return }
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
        controlTimer?.invalidate(); controlTimer = nil
        audio?.stop(); audio = nil
        link = nil
        image = nil; videoAgeMs = nil; rttMs = nil; fps = 0; level = 0; ages.removeAll(); robotLine = ""
        muted = true
        if reason != "audio_failed" { audioNote = nil }
        audioInUse = ""
        headOffered = false; driveOffered = false; driveLine = ""; baseLine = ""; headNote = nil; holdLine = nil
        steering = Steering(); headKeys.removeAll(); pacer = SetpointPacer(); lastFrameAt = nil
        phase = .ended(reason)
    }

    // MARK: Head and wheels

    /// Ten times a second: the wheels' setpoint (with the app's own holds) and the head's newest target.
    private func controlTick() {
        guard phase == .inCall, let link else { return }
        readController()
        let dt = 1 / Self.controlHz
        // Head: held keys and the right stick move the target at a look's pace.
        let rates: [String: Double] = ["neck": 0.8, "pitch": 0.8, "tilt": 0.8, "lift": 0.4]
        for (field, rate) in rates {
            var direction = (headKeys.contains(field + "+") ? 1.0 : 0) - (headKeys.contains(field + "-") ? 1.0 : 0)
            if field == "neck", let pad = rightStick { direction += pad.x }
            if field == "pitch", let pad = rightStick { direction += pad.y }
            if direction != 0 { aim.nudge(field, by: direction * rate * dt) }
        }
        if headOffered, aim != sentAim {
            link.sendHead(aim.changes(since: sentAim))
            sentAim = aim
        }
        // Wheels.
        guard driveOffered else { return }
        var (lin, ang) = steering.setpoint(speed: speed)
        holdLine = nil
        if NSApp.keyWindow == nil {
            holdLine = "Wheels held: Rex Call isn't the front window."
        } else if videoExpected, lastFrameAt.map({ Date().timeIntervalSince($0) > Self.pictureLateSeconds }) ?? true {
            holdLine = "Wheels held: the picture is late."
        }
        if holdLine != nil { lin = 0; ang = 0 }
        if pacer.shouldSend(lin: lin, ang: ang) { link.sendDrive(lin: lin, ang: ang) }
    }

    /// Where the robot says the head is, adopted while the caller is not moving it: the home pose leaves
    /// some parts unknown (the visor), and the robot levels the head itself before the wheels move.
    private func followHead(_ reading: [String: Any]) {
        guard headOffered, aim == sentAim, headKeys.isEmpty, !dragging, rightStick.map({ $0 == (0, 0) }) ?? true else { return }
        var next = aim
        for field in HeadAim.fields {
            if let value = reading[field] as? Double { next[field] = value }
            else if let value = reading[field] as? Int { next[field] = Double(value) }
        }
        aim = next
        sentAim = next
    }

    var dragging = false

    /// Space: the wheels stop now and stay stopped until every drive key is up.
    func stopWheels() {
        guard let link else { return }
        link.sendStop()
        steering = Steering()
        pacer.releaseAfterStop()
    }

    func centerHead() { aim = home }

    func toggleVisor() { aim.visor = aim.visor > 0.5 ? 0 : max(home.visor, 0.6) }

    /// A drag on the picture: right looks right, up looks up; the whole width is a bit over a full turn's half.
    func aimBy(dx: Double, dy: Double, from start: HeadAim) {
        var next = start
        next["neck"] = start.neck + dx * 1.2
        next["pitch"] = start.pitch - dy * 1.2
        aim = next
    }

    /// The window lost focus: nothing stays pressed.
    func releaseAll() {
        steering = Steering()
        headKeys.removeAll()
        if pushToTalk { pushToTalk = false; muted = true }
    }

    /// Keys while on a call. Returns true when the key was ours.
    func key(_ name: String, down: Bool, repeated: Bool) -> Bool {
        switch name {
        case "w": steering.forward = down
        case "s": steering.back = down
        case "a": steering.left = down
        case "d": steering.right = down
        case "space": if down && !repeated { stopWheels() }
        case "left": set("neck-", down)
        case "right": set("neck+", down)
        case "up": set("pitch+", down)
        case "down": set("pitch-", down)
        case "q": set("tilt-", down)
        case "e": set("tilt+", down)
        case "r": set("lift+", down)
        case "f": set("lift-", down)
        case "c": if down && !repeated { centerHead() }
        case "v": if down && !repeated { toggleVisor() }
        case "m": if down && !repeated { toggleMute() }
        case "t":
            if down && !repeated && muted { pushToTalk = true; muted = false }
            if !down && pushToTalk { pushToTalk = false; muted = true }
        default: return false
        }
        return true
    }

    private func set(_ key: String, _ down: Bool) {
        if down { headKeys.insert(key) } else { headKeys.remove(key) }
    }

    // A game controller: left stick drives, right stick aims, B stops, Y centres, X the visor, A talks.
    private var rightStick: (x: Double, y: Double)?

    private func readController() {
        guard let pad = GCController.current?.extendedGamepad else { rightStick = nil; steering.stick = (0, 0); return }
        steering.stick = (Double(pad.leftThumbstick.xAxis.value), Double(pad.leftThumbstick.yAxis.value))
        let rx = Double(pad.rightThumbstick.xAxis.value), ry = Double(pad.rightThumbstick.yAxis.value)
        rightStick = (abs(rx) < Steering.deadZone ? 0 : rx, abs(ry) < Steering.deadZone ? 0 : ry)
        func pressed(_ name: String, _ button: GCControllerButtonInput) -> Bool {
            let now = button.isPressed, before = padButtons[name] ?? false
            padButtons[name] = now
            return now && !before
        }
        if pressed("b", pad.buttonB) { stopWheels() }
        if pressed("y", pad.buttonY) { centerHead() }
        if pressed("x", pad.buttonX) { toggleVisor() }
        let talk = pad.buttonA.isPressed
        if talk != (padButtons["a"] ?? false) {
            padButtons["a"] = talk
            _ = key("t", down: talk, repeated: false)
        }
    }

    // MARK: Words

    static func describeWheels(_ state: [String: Any]) -> String {
        var parts: [String] = []
        if state["moving"] as? Bool == true {
            let lin = state["lin_mps"] as? Double ?? 0, turn = state["ang_dps"] as? Double ?? 0
            parts.append("Moving \(String(format: "%.2f", lin)) m/s" + (turn != 0 ? ", turning \(Int(turn))°/s" : ""))
        } else {
            parts.append(state["stopped"] as? Bool == true ? "Stopped: let go of the keys to drive again" : "Wheels still")
        }
        if let held = state["held"] as? [String], !held.isEmpty {
            parts.append("held: " + held.map(explainHold).joined(separator: ", "))
        }
        if let refused = state["refused"] as? String, refused != "stopped" {
            parts.append("refused: " + explainRefusal(refused))
        }
        return parts.joined(separator: " · ")
    }

    static func describeBase(_ base: [String: Any]) -> String {
        var parts: [String] = []
        if let percent = base["battery_percent"] as? Int {
            parts.append("Battery \(percent)%" + ((base["charging"] as? Bool) == true ? " (charging)" : ""))
        }
        if let space = base["clearance_m"] as? [String: Any] {
            func metres(_ key: String) -> String? { (space[key] as? Double).map { String(format: "%.1f m", $0) } }
            if let front = metres("front_m") { parts.append("ahead \(front)") }
            if let rear = metres("rear_m") { parts.append("behind \(rear)") }
        }
        if let notes = base["sensor_notes"] as? [String], !notes.isEmpty { parts.append(notes.joined(separator: ", ")) }
        return parts.joined(separator: " · ")
    }

    static func explainHold(_ hold: String) -> String {
        switch hold {
        case "path_blocked", "blocked_front": return "something ahead"
        case "blocked_back": return "something behind"
        case "turn_path_blocked": return "no room to turn"
        case "front_clearance_unknown": return "can't see ahead"
        case "charging": return "on the charger, turning only"
        default: return hold
        }
    }

    static func explainRefusal(_ reason: String) -> String {
        switch reason {
        case "link_not_measured": return "measuring the link first"
        case "link_too_slow": return "the link is too slow to drive"
        case "setpoint_stale": return "your steering arrived late"
        case "charging": return "he's on the charger"
        case "drive_telemetry_stale": return "the base stopped reporting"
        case "controller_estop": return "emergency stop on the base"
        case "gamepad_in_control": return "his own gamepad has the wheels"
        case "front_depth_matrix_unavailable": return "his front depth sensor is out"
        default: return reason
        }
    }

    static func explainHead(_ reason: String) -> String {
        switch reason {
        case "head_held_while_driving": return "The head stays level while the wheels move."
        case "head_not_available": return "The head isn't available on this call."
        default: return "Head: \(reason)"
        }
    }

    static func explain(_ reason: String) -> String {
        switch reason {
        case "hung_up": return "Call ended."
        case "wrong_token": return "Rex refused the token. Paste the telepresence_token line from the robot's local/secrets.toml."
        case "busy": return "Someone else is already on a call with Rex."
        case "rex_awake": return "Rex is awake (talking, playing or on guard). A call can only start while he's asleep."
        case "supervisor_busy": return "Rex's supervisor didn't answer in time. Try again."
        case "launch_failed": return "Rex's supervisor couldn't start the call program."
        case "call_never_answered": return "Rex was started but the call never answered. The supervisor's note above says how it ended."
        case "connection_refused", "closed_during_sign_in": return "Rex isn't taking calls. Is telepresence running on the robot?"
        case "unreachable", "timed_out": return "Can't reach Rex. Is the VPN connected, and is the address right?"
        case "robot_closed": return "The call ended at Rex's end."
        case "robot_silent": return "Lost the connection to Rex."
        case "microphone_denied": return "This Mac's microphone is off for Rex Call. Allow it in System Settings → Privacy & Security → Microphone."
        case "audio_failed": return "This Mac's sound could not start. Try another microphone or speaker below."
        case "no_token": return "Paste the telepresence token first."
        case "bad_address": return "The address needs to look like 192.168.1.50:47800."
        default: return "The call ended (\(reason))."
        }
    }
}
