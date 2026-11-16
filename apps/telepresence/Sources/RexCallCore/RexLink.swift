// One call's connection to the robot over TCP (Network framework), on one serial queue.
//
// Sign-in: answer the robot's challenge with the HMAC proof, then wait for its welcome. After that,
// pings every second (round trip and the robot's clock offset), answers to the robot's pings, and the
// owner's voice. Audio waiting to go out past AUDIO_BACKLOG seconds is dropped, so a slow link costs
// words rather than delay. Silence from the robot for SILENT_SECONDS ends the call.
import Foundation
import Network

public final class RexLink: @unchecked Sendable {
    public static let signInSeconds = 5.0
    public static let silentSeconds = 5.0
    public static let audioBacklog = 0.5

    public struct Clock {
        public static func now() -> Double { ProcessInfo.processInfo.systemUptime }
    }

    public enum Event {
        case welcome([String: Any])
        case audio(Data)                       // Room PCM.
        case video(Data, age: Double?)         // JPEG, and how old it is by the robot's clock, when known.
        case status([String: Any])
        case closed(String)
    }

    public let queue = DispatchQueue(label: "rexcall.link")
    private let connection: NWConnection
    private let token: String
    private let client: String
    private var reader = FrameReader()
    private var phase = "connecting"
    private var timer: DispatchSourceTimer?
    private var lastHeard = Clock.now()
    private var pings: [Int: Double] = [:]
    private var pingID = 0
    private var audioInFlight = 0
    private var audioSequence: UInt32 = 0
    private var finished = false
    public var onEvent: (Event) -> Void = { _ in }

    // Measurements, read on the queue (snapshot()).
    private var rtts: [Double] = []
    private var offsets: [Double] = []
    public private(set) var audioDropped = 0
    public private(set) var audioSent = 0
    public private(set) var framesIn = 0

    public init(host: String, port: UInt16, token: String, client: String = "rex-call") {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        tcp.connectionTimeout = 5
        connection = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: port)!,
                                  using: NWParameters(tls: nil, tcp: tcp))
        self.token = token
        self.client = client
    }

    public func start() {
        connection.stateUpdateHandler = { [weak self] state in
            guard let self else { return }
            switch state {
            case .ready:
                self.phase = "challenge"
                self.lastHeard = Clock.now()
                self.receive()
                self.startTimer()
            case .failed(let error):
                self.finish(Self.reason(error))
            case .waiting(let error):
                self.finish(Self.reason(error))   // Refused or unreachable: the caller decides whether to retry.
            case .cancelled:
                self.finish("closed")
            default:
                break
            }
        }
        connection.start(queue: queue)
    }

    static func reason(_ error: NWError) -> String {
        if case .posix(let code) = error {
            switch code {
            case .ECONNREFUSED: return "connection_refused"
            case .ETIMEDOUT: return "timed_out"
            case .EHOSTUNREACH, .ENETUNREACH: return "unreachable"
            default: return "network_\(code.rawValue)"
            }
        }
        return "network_error"
    }

    // MARK: Sending (any thread)

    public func sendJSON(_ object: [String: Any], then: (() -> Void)? = nil) {
        queue.async {
            guard !self.finished, let data = try? Wire.json(object) else { then?(); return }
            self.connection.send(content: data, completion: .contentProcessed { _ in then?() })
        }
    }

    /// The owner's voice: 16 kHz mono s16le.
    public func sendAudio(_ pcm: Data) {
        queue.async {
            guard !self.finished, self.phase == "signed_in" else { return }
            let backlog = Int(Self.audioBacklog * Double(Wire.audioRate) * 2)
            guard self.audioInFlight + pcm.count <= backlog else { self.audioDropped += 1; return }
            let data = Wire.stamped(.audio, sequence: self.audioSequence, at: Clock.now(), pcm)
            self.audioSequence &+= 1
            self.audioInFlight += pcm.count
            self.audioSent += 1
            self.connection.send(content: data, completion: .contentProcessed { _ in
                self.queue.async { self.audioInFlight -= pcm.count }
            })
        }
    }

    /// Tell the robot the call is over (he parks), then close.
    public func hangUp() {
        let done = DispatchSemaphore(value: 0)
        sendJSON(["type": "hangup"]) { done.signal() }
        _ = done.wait(timeout: .now() + 1)
        queue.sync { self.finish("hung_up") }
    }

    static func median<S: Collection>(_ values: S) -> Double? where S.Element == Double {
        let sorted = values.sorted()
        return sorted.isEmpty ? nil : sorted[sorted.count / 2]
    }

    /// Round trip (median of the last ten, and the worst) in milliseconds.
    public func snapshot() -> (rttMs: Double?, rttMaxMs: Double?) {
        queue.sync { (Self.median(rtts.suffix(10)).map { $0 * 1000 }, rtts.max().map { $0 * 1000 }) }
    }

    public func roundTrips() -> [Double] { queue.sync { rtts } }

    // MARK: Receiving (queue)

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 256 * 1024) { [weak self] data, _, complete, error in
            guard let self, !self.finished else { return }
            if let data, !data.isEmpty {
                self.lastHeard = Clock.now()
                self.reader.append(data)
                do {
                    while let (kind, payload) = try self.reader.next() {
                        self.framesIn += 1
                        try self.handle(kind, payload)
                        if self.finished { return }
                    }
                } catch let error as WireError {
                    self.finish("protocol:\(error)")
                    return
                } catch {
                    self.finish("protocol:invalid")
                    return
                }
            }
            if complete { self.finish(self.phase == "signed_in" ? "robot_closed" : "closed_during_sign_in"); return }
            if error != nil { self.finish("receive_failed"); return }
            self.receive()
        }
    }

    private func handle(_ kind: Kind, _ payload: Data) throws {
        switch kind {
        case .audio:
            onEvent(.audio(try Wire.unstamp(payload).body))
        case .video:
            let (_, at, jpeg) = try Wire.unstamp(payload)
            let offset = Self.median(offsets.suffix(15))   // Robot clock minus this Mac's.
            onEvent(.video(jpeg, age: offset.map { Clock.now() + $0 - at }))
        case .json:
            try handleJSON(Wire.decodeJSON(payload))
        }
    }

    private func handleJSON(_ message: [String: Any]) throws {
        let type = message["type"] as? String ?? ""
        switch (phase, type) {
        case (_, "refused"):
            finish(message["reason"] as? String ?? "refused")
        case ("challenge", "challenge"):
            guard message["version"] as? Int == Wire.version, let nonce = (message["nonce"] as? String).flatMap(Wire.hex) else {
                finish("unexpected_challenge"); return
            }
            phase = "welcome"
            let hello: [String: Any] = ["type": "hello", "version": Wire.version, "proof": Wire.proof(token: token, nonce: nonce), "client": client]
            connection.send(content: try Wire.json(hello), completion: .contentProcessed { _ in })
        case ("welcome", "welcome"):
            phase = "signed_in"
            reader.signedIn = true
            onEvent(.welcome(message))
        case ("signed_in", "ping"):
            guard let id = message["id"] as? Int else { return }
            connection.send(content: try Wire.json(["type": "pong", "id": id, "t": message["t"] ?? NSNull()]),
                            completion: .contentProcessed { _ in })
        case ("signed_in", "pong"):
            let now = Clock.now()
            if let id = message["id"] as? Int, let sent = pings.removeValue(forKey: id) {
                rtts.append(now - sent)
                if rtts.count > 600 { rtts.removeFirst(rtts.count - 600) }
                if let robot = message["robot_t"] as? Double {
                    offsets.append(robot - (sent + now) / 2)
                    if offsets.count > 60 { offsets.removeFirst(offsets.count - 60) }
                }
            }
        case ("signed_in", "status"):
            onEvent(.status(message))
        case ("signed_in", _):
            break   // A newer robot's message this app does not know yet.
        default:
            finish("unexpected_\(type)")
        }
    }

    private func startTimer() {
        let timer = DispatchSource.makeTimerSource(queue: queue)
        let started = Clock.now()
        timer.schedule(deadline: .now() + 1, repeating: 1)
        timer.setEventHandler { [weak self] in
            guard let self, !self.finished else { return }
            let now = Clock.now()
            if self.phase != "signed_in" {
                if now - started > Self.signInSeconds { self.finish("sign_in_timed_out") }
                return
            }
            if now - self.lastHeard > Self.silentSeconds { self.finish("robot_silent"); return }
            self.pingID += 1
            self.pings[self.pingID] = now
            self.pings = self.pings.filter { now - $0.value < 10 }
            if let data = try? Wire.json(["type": "ping", "id": self.pingID, "t": now]) {
                self.connection.send(content: data, completion: .contentProcessed { _ in })
            }
        }
        timer.resume()
        self.timer = timer
    }

    private func finish(_ reason: String) {
        guard !finished else { return }
        finished = true
        timer?.cancel()
        connection.cancel()
        onEvent(.closed(reason))
    }

    /// Close from any thread without telling the robot (it treats the call as dropped and may wait for a reconnect).
    public func close() { queue.async { self.finish("closed_by_app") } }
}
