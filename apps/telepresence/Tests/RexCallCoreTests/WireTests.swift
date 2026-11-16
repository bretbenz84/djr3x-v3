// The app's wire format against reference bytes from src/r3x/telepresence/protocol.py, and the
// jitter buffer's behaviour against the robot's (src/r3x/telepresence/speaker.py). No network or audio.
import XCTest
@testable import RexCallCore

final class WireTests: XCTestCase {
    let token = "synthetic-telepresence-token-0123456789abcdef"

    func testProofMatchesThePythonRobot() {
        // protocol.proof(token, b'n' * 16)
        XCTAssertEqual(Wire.proof(token: token, nonce: Data(repeating: UInt8(ascii: "n"), count: 16)),
                       "42af2f7f651af94b9fec2b37a0d247e7545b54f26c9f28dad087089d72d444b6")
    }

    func testStampedAudioMatchesThePythonBytesAndRoundTrips() throws {
        // protocol.encode_stamped('A', 7, 12.5, b'\x01\x00' * 4).hex()
        let pcm = Data([1, 0, 1, 0, 1, 0, 1, 0])
        let frame = Wire.stamped(.audio, sequence: 7, at: 12.5, pcm)
        XCTAssertEqual(frame, Wire.hex("41000000140000000740290000000000000100010001000100"))
        var reader = FrameReader()
        reader.signedIn = true
        reader.append(frame)
        let (kind, payload) = try XCTUnwrap(reader.next())
        XCTAssertEqual(kind, .audio)
        let (sequence, at, body) = try Wire.unstamp(payload)
        XCTAssertEqual(sequence, 7); XCTAssertEqual(at, 12.5); XCTAssertEqual(body, pcm)
    }

    func testFramesSplitAcrossReadsAreReassembled() throws {
        // protocol.encode_json({'type': 'ping', 'id': 1, 't': 2.5}).hex()
        let bytes = try XCTUnwrap(Wire.hex("4a0000001e7b2274797065223a2270696e67222c226964223a312c2274223a322e357d"))
        var reader = FrameReader()
        for byte in bytes.dropLast() {
            reader.append(Data([byte]))
            XCTAssertNil(try reader.next())
        }
        reader.append(bytes.suffix(1))
        let (kind, payload) = try XCTUnwrap(reader.next())
        XCTAssertEqual(kind, .json)
        let message = try Wire.decodeJSON(payload)
        XCTAssertEqual(message["type"] as? String, "ping")
        XCTAssertEqual(message["id"] as? Int, 1)
    }

    func testBeforeSignInOnlySmallJSONIsRead() {
        var reader = FrameReader()
        reader.append(Wire.stamped(.video, sequence: 0, at: 0, Data()))
        XCTAssertThrowsError(try reader.next()) { XCTAssertEqual($0 as? WireError, .unknownKind) }
        var big = FrameReader()
        big.append(Wire.frame(.json, Data(repeating: 0x20, count: 5000)))
        XCTAssertThrowsError(try big.next()) { XCTAssertEqual($0 as? WireError, .frameTooLarge) }
        var oversized = FrameReader()
        oversized.signedIn = true
        oversized.append(Data([Kind.audio.rawValue, 0, 0, 0x40, 1]))   // 16 KiB + 1
        XCTAssertThrowsError(try oversized.next()) { XCTAssertEqual($0 as? WireError, .frameTooLarge) }
    }

    func testAddresses() throws {
        XCTAssertEqual(try Wire.parseAddress("192.168.1.50:47800").port, 47800)
        XCTAssertEqual(try Wire.parseAddress("[fd00::5]:47800").host, "fd00::5")
        for bad in ["192.168.1.50", "fd00::5:47800", "rex:80", ":47800"] {
            XCTAssertThrowsError(try Wire.parseAddress(bad), bad)
        }
    }

    func testJitterBufferPrimesCutsBacklogAndCountsUnderruns() {
        let buffer = JitterBuffer()
        var out = [Float](repeating: 1, count: 320)
        let voice = Data((0..<640).map { $0 % 2 == 0 ? 0x10 : 0x00 })   // 20 ms of 16
        buffer.write(voice)
        out.withUnsafeMutableBufferPointer { buffer.read(into: $0.baseAddress!, count: 320) }
        XCTAssertEqual(out.max(), 0)                                      // Priming: silence.
        for _ in 0..<6 { buffer.write(voice) }                            // 140 ms.
        out.withUnsafeMutableBufferPointer { buffer.read(into: $0.baseAddress!, count: 320) }
        XCTAssertEqual(out[0], 16.0 / 32768, accuracy: 1e-7)
        for _ in 0..<7 { out.withUnsafeMutableBufferPointer { buffer.read(into: $0.baseAddress!, count: 320) } }
        XCTAssertEqual(buffer.underruns, 1)
        buffer.write(Data(count: 32000))                                  // One second at once.
        XCTAssertEqual(buffer.bufferedSeconds, JitterBuffer.startSeconds, accuracy: 0.001)
        XCTAssertGreaterThan(buffer.droppedSeconds, 0.8)
    }
}
