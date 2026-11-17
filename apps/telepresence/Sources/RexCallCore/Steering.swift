// What the caller's keys, mouse and game controller ask of Rex's wheels and head, as numbers on the
// robot's scale (docs/TELEPRESENCE_PLAN.md, "Wire messages for the app"). No devices: the app feeds it.
import Foundation

/// Wheels: keys or a stick to a setpoint. lin forward positive; ang positive turns left.
public struct Steering: Equatable {
    public var forward = false, back = false, left = false, right = false
    public var stick: (x: Double, y: Double) = (0, 0)   // A game controller's left stick, -1...1, up positive.
    public static let deadZone = 0.12

    public init() {}

    public static func == (a: Steering, b: Steering) -> Bool {
        a.forward == b.forward && a.back == b.back && a.left == b.left && a.right == b.right
            && a.stick.x == b.stick.x && a.stick.y == b.stick.y
    }

    /// speed: the share of the robot's caps the caller allows (the speed slider), 0.1...1.
    public func setpoint(speed: Double) -> (lin: Double, ang: Double) {
        var lin = (forward ? 1.0 : 0) - (back ? 1.0 : 0)
        var ang = (left ? 1.0 : 0) - (right ? 1.0 : 0)
        let x = abs(stick.x) < Self.deadZone ? 0 : stick.x
        let y = abs(stick.y) < Self.deadZone ? 0 : stick.y
        if lin == 0 && ang == 0 {
            lin = y
            ang = -x   // Stick right turns right, which is negative ang.
        }
        let s = max(0.1, min(1, speed))
        return (max(-1, min(1, lin)) * s, max(-1, min(1, ang)) * s)
    }

    public var active: Bool { forward || back || left || right || abs(stick.x) >= Self.deadZone || abs(stick.y) >= Self.deadZone }
}

/// When to send a wheel setpoint, at 10 a second: while steering, and a few zeros after letting go so
/// the robot hears the release (which also ends a Stop's hold). Silence after that.
public struct SetpointPacer {
    public static let trailingZeros = 5   // Half a second of zeros.
    private var zerosLeft = 0

    public init() {}

    public mutating func shouldSend(lin: Double, ang: Double) -> Bool {
        if lin != 0 || ang != 0 {
            zerosLeft = Self.trailingZeros
            return true
        }
        if zerosLeft > 0 {
            zerosLeft -= 1
            return true
        }
        return false
    }

    /// After a Stop: send zeros again so the hold ends once the keys are up.
    public mutating func releaseAfterStop() { zerosLeft = Self.trailingZeros }
}

/// Where the caller wants the head: absolute targets, clamped to the robot's scale.
public struct HeadAim: Equatable {
    public var neck = 0.0, pitch = 0.0, tilt = 0.0, lift = 0.0, visor = 1.0
    public static let fields = ["neck", "pitch", "tilt", "lift", "visor"]

    public init() {}

    /// The welcome's controls.head.home (the drive stance's level pose); missing values keep the defaults.
    public init(home: [String: Any]?) {
        for field in Self.fields {
            if let value = home?[field] as? Double { self[field] = value }
            else if let value = home?[field] as? Int { self[field] = Double(value) }
        }
    }

    public static func range(_ field: String) -> ClosedRange<Double> {
        field == "lift" || field == "visor" ? 0...1 : -1...1
    }

    public subscript(field: String) -> Double {
        get {
            switch field {
            case "neck": return neck
            case "pitch": return pitch
            case "tilt": return tilt
            case "lift": return lift
            default: return visor
            }
        }
        set {
            let range = Self.range(field)
            let value = min(range.upperBound, max(range.lowerBound, newValue))
            switch field {
            case "neck": neck = value
            case "pitch": pitch = value
            case "tilt": tilt = value
            case "lift": lift = value
            default: visor = value
            }
        }
    }

    public mutating func nudge(_ field: String, by amount: Double) { self[field] = self[field] + amount }

    /// The fields that differ from `other`, rounded, as a head message's targets.
    public func changes(since other: HeadAim?) -> [String: Double] {
        var out: [String: Double] = [:]
        for field in Self.fields where other == nil || abs(self[field] - other![field]) > 0.0005 {
            out[field] = (self[field] * 1000).rounded() / 1000
        }
        return out
    }
}
