// The room's voice played as it arrives, as src/r3x/telepresence/speaker.py does on the robot:
// silence until START seconds are buffered, silence again after running dry (a short gap rather
// than crackle), and a backlog past MAX cut back to START so delay never builds up.
import Foundation
import os

public final class JitterBuffer: @unchecked Sendable {
    public static let startSeconds = 0.12
    public static let maxSeconds = 0.4

    private let lock = OSAllocatedUnfairLock()
    private var samples: [Int16] = []
    private var priming = true
    private let startCount: Int
    private let maxCount: Int
    public private(set) var underruns = 0
    public private(set) var droppedSeconds = 0.0

    public init(rate: Int = Wire.audioRate) {
        startCount = Int(Self.startSeconds * Double(rate))
        maxCount = Int(Self.maxSeconds * Double(rate))
    }

    /// Little-endian 16-bit PCM from the link.
    public func write(_ pcm: Data) {
        let count = pcm.count / 2
        var incoming = [Int16](repeating: 0, count: count)
        incoming.withUnsafeMutableBytes { _ = pcm.copyBytes(to: $0, count: count * 2) }
        lock.withLock {
            samples.append(contentsOf: incoming.map { Int16(littleEndian: $0) })
            if samples.count > maxCount {
                let drop = samples.count - startCount
                samples.removeFirst(drop)
                droppedSeconds += Double(drop) / Double(Wire.audioRate)
            }
        }
    }

    public func clear() {
        lock.withLock { samples.removeAll(keepingCapacity: true); priming = true }
    }

    public var bufferedSeconds: Double {
        lock.withLock { Double(samples.count) / Double(Wire.audioRate) }
    }

    /// Fills `out` (count samples) as floats in -1...1, silence where there is nothing to play.
    public func read(into out: UnsafeMutablePointer<Float>, count: Int) {
        lock.withLock {
            if priming && samples.count >= startCount { priming = false }
            var taken = 0
            if !priming {
                taken = min(count, samples.count)
                for i in 0..<taken { out[i] = Float(samples[i]) / 32768 }
                samples.removeFirst(taken)
                if taken < count { priming = true; underruns += 1 }
            }
            for i in taken..<count { out[i] = 0 }
        }
    }
}
