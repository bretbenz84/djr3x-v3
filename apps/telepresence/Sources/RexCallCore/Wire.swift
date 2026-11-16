// The telepresence wire format, matching src/r3x/telepresence/protocol.py byte for byte.
//
// A frame is one kind byte, a four-byte big-endian payload length and the payload:
//   J  one JSON object (UTF-8)
//   A  audio: u32 sequence, f64 sender clock (big-endian), then 16 kHz mono s16le PCM
//   V  video: u32 sequence, f64 robot clock at capture, then one JPEG
import CryptoKit
import Foundation

public enum Kind: UInt8 {
    case json = 0x4A    // J
    case audio = 0x41   // A
    case video = 0x56   // V

    public var limit: Int {
        switch self {
        case .json: return 16 * 1024
        case .audio: return 16 * 1024
        case .video: return 2 * 1024 * 1024
        }
    }
}

public enum WireError: Error, Equatable, CustomStringConvertible {
    case unknownKind
    case frameTooLarge
    case shortStamped
    case invalidJSON
    case invalidAddress(String)

    public var description: String {
        switch self {
        case .unknownKind: return "unexpected_frame_kind"
        case .frameTooLarge: return "frame_too_large"
        case .shortStamped: return "short_stamped_frame"
        case .invalidJSON: return "invalid_json"
        case .invalidAddress(let why): return why
        }
    }
}

public enum Wire {
    public static let version = 1
    public static let audioRate = 16000
    public static let headerSize = 5
    public static let stampSize = 12
    static let domain = Data("r3x-telepresence-v1:".utf8)

    public static func frame(_ kind: Kind, _ payload: Data) -> Data {
        var data = Data(capacity: headerSize + payload.count)
        data.append(kind.rawValue)
        var size = UInt32(payload.count).bigEndian
        withUnsafeBytes(of: &size) { data.append(contentsOf: $0) }
        data.append(payload)
        return data
    }

    public static func json(_ object: [String: Any]) throws -> Data {
        let payload = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
        return frame(.json, payload)
    }

    public static func stamped(_ kind: Kind, sequence: UInt32, at: Double, _ body: Data) -> Data {
        var payload = Data(capacity: stampSize + body.count)
        var seq = sequence.bigEndian
        var bits = at.bitPattern.bigEndian
        withUnsafeBytes(of: &seq) { payload.append(contentsOf: $0) }
        withUnsafeBytes(of: &bits) { payload.append(contentsOf: $0) }
        payload.append(body)
        return frame(kind, payload)
    }

    public static func unstamp(_ payload: Data) throws -> (sequence: UInt32, at: Double, body: Data) {
        guard payload.count >= stampSize else { throw WireError.shortStamped }
        let bytes = [UInt8](payload.prefix(stampSize))
        let seq = bytes[0..<4].reduce(UInt32(0)) { $0 << 8 | UInt32($1) }
        let bits = bytes[4..<12].reduce(UInt64(0)) { $0 << 8 | UInt64($1) }
        return (seq, Double(bitPattern: bits), payload.dropFirst(stampSize))
    }

    public static func decodeJSON(_ payload: Data) throws -> [String: Any] {
        guard let object = try? JSONSerialization.jsonObject(with: payload),
              let message = object as? [String: Any], message["type"] is String else {
            throw WireError.invalidJSON
        }
        return message
    }

    /// HMAC-SHA256 of the robot's nonce under the shared token, as hex: the token never crosses the wire.
    public static func proof(token: String, nonce: Data) -> String {
        let key = SymmetricKey(data: Data(token.utf8))
        let code = HMAC<SHA256>.authenticationCode(for: domain + nonce, using: key)
        return code.map { String(format: "%02x", $0) }.joined()
    }

    /// "192.168.1.50:47800" or "[fd00::5]:47800", as telepresence_listen on the robot.
    public static func parseAddress(_ text: String) throws -> (host: String, port: UInt16) {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        guard let colon = trimmed.lastIndex(of: ":") else { throw WireError.invalidAddress("needs host:port") }
        var host = String(trimmed[..<colon])
        let portText = trimmed[trimmed.index(after: colon)...]
        if host.hasPrefix("[") && host.hasSuffix("]") {
            host = String(host.dropFirst().dropLast())
        } else if host.contains(":") {
            throw WireError.invalidAddress("IPv6 needs [brackets]")
        }
        guard !host.isEmpty else { throw WireError.invalidAddress("needs host:port") }
        guard let port = UInt16(portText), port >= 1024 else { throw WireError.invalidAddress("port 1024-65535") }
        return (host, port)
    }

    public static func hex(_ text: String) -> Data? {
        guard text.count % 2 == 0 else { return nil }
        var data = Data(capacity: text.count / 2)
        var index = text.startIndex
        while index < text.endIndex {
            let next = text.index(index, offsetBy: 2)
            guard let byte = UInt8(text[index..<next], radix: 16) else { return nil }
            data.append(byte)
            index = next
        }
        return data
    }
}

/// Splits a byte stream into frames; bytes may arrive in any pieces.
public struct FrameReader {
    var buffer = Data()
    public var signedIn = false   // Before sign-in only small JSON frames are read.

    public init() {}

    public mutating func append(_ data: Data) { buffer.append(data) }

    public mutating func next() throws -> (Kind, Data)? {
        guard buffer.count >= Wire.headerSize else { return nil }
        let head = [UInt8](buffer.prefix(Wire.headerSize))
        guard let kind = Kind(rawValue: head[0]), signedIn || kind == .json else { throw WireError.unknownKind }
        let size = Int(head[1..<5].reduce(UInt32(0)) { $0 << 8 | UInt32($1) })
        guard size <= (signedIn ? kind.limit : 4 * 1024) else { throw WireError.frameTooLarge }
        guard buffer.count >= Wire.headerSize + size else { return nil }
        let start = buffer.startIndex + Wire.headerSize
        let payload = Data(buffer[start..<(start + size)])
        buffer.removeFirst(Wire.headerSize + size)
        return (kind, payload)
    }
}
