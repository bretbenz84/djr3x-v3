// The supervisor's telepresence trigger (stage 4): one port above the call, the same sign-in, then
// one JSON request ({"type": "status" | "start" | "stop"}) and one JSON answer, as
// src/r3x/telepresence/trigger.py serves it. It starts a call only while Rex is asleep.
import Foundation
import Network

public enum TriggerError: Error, Equatable, CustomStringConvertible {
    case unreachable(String)      // No supervisor answering (none installed, or the robot is off).
    case refused(String)          // The supervisor's or sign-in's reason, such as wrong_token.
    case timedOut

    public var description: String {
        switch self {
        case .unreachable(let why): return why
        case .refused(let why): return why
        case .timedOut: return "timed_out"
        }
    }
}

public final class Trigger: @unchecked Sendable {
    public static let timeoutSeconds = 12.0   // Sign-in (5 s) plus the supervisor's answer (5 s).

    private let connection: NWConnection
    private let token: String
    private let request: [String: Any]
    private let queue = DispatchQueue(label: "rexcall.trigger")
    private var reader = FrameReader()
    private var phase = "challenge"
    private var done = false
    private var completion: ((Result<[String: Any], TriggerError>) -> Void)?

    /// callPort: the call's port; the trigger listens one above it.
    public init(host: String, callPort: UInt16, token: String, request: [String: Any]) {
        let tcp = NWProtocolTCP.Options()
        tcp.noDelay = true
        tcp.connectionTimeout = 4
        connection = NWConnection(host: NWEndpoint.Host(host), port: NWEndpoint.Port(rawValue: callPort &+ 1)!,
                                  using: NWParameters(tls: nil, tcp: tcp))
        self.token = token
        self.request = request
    }

    /// One request; the answer arrives on an internal queue.
    public static func ask(host: String, callPort: UInt16, token: String, _ request: [String: Any],
                           completion: @escaping (Result<[String: Any], TriggerError>) -> Void) {
        let trigger = Trigger(host: host, callPort: callPort, token: token, request: request)
        trigger.completion = completion
        trigger.start()
    }

    private func start() {
        connection.stateUpdateHandler = { [self] state in
            switch state {
            case .ready: receive()
            case .failed(let error), .waiting(let error): finish(.failure(.unreachable(RexLink.reason(error))))
            default: break
            }
        }
        connection.start(queue: queue)
        queue.asyncAfter(deadline: .now() + Self.timeoutSeconds) { [self] in finish(.failure(.timedOut)) }
    }

    private func receive() {
        connection.receive(minimumIncompleteLength: 1, maximumLength: 65536) { [self] data, _, complete, error in
            guard !done else { return }
            if let data, !data.isEmpty {
                reader.append(data)
                do {
                    while let (_, payload) = try reader.next() {
                        try handle(Wire.decodeJSON(payload))
                        if done { return }
                    }
                } catch {
                    finish(.failure(.refused("protocol:\(error)"))); return
                }
            }
            if complete || error != nil { finish(.failure(.refused("closed_during_sign_in"))); return }
            receive()
        }
    }

    private func handle(_ message: [String: Any]) throws {
        let type = message["type"] as? String ?? ""
        if type == "refused" { finish(.failure(.refused(message["reason"] as? String ?? "refused"))); return }
        switch phase {
        case "challenge":
            guard type == "challenge", let nonce = (message["nonce"] as? String).flatMap(Wire.hex) else {
                finish(.failure(.refused("unexpected_challenge"))); return
            }
            phase = "welcome"
            let hello: [String: Any] = ["type": "hello", "version": Wire.version, "proof": Wire.proof(token: token, nonce: nonce),
                                        "client": "rex-call-mac"]
            connection.send(content: try Wire.json(hello), completion: .contentProcessed { _ in })
        case "welcome":
            guard type == "welcome" else { finish(.failure(.refused("unexpected_\(type)"))); return }
            phase = "answer"
            reader.signedIn = true
            connection.send(content: try Wire.json(request), completion: .contentProcessed { _ in })
        default:
            finish(.success(message))
        }
    }

    private func finish(_ result: Result<[String: Any], TriggerError>) {
        guard !done else { return }
        done = true
        connection.cancel()
        completion?(result)
        completion = nil
    }
}
