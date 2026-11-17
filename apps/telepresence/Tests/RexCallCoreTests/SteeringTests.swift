// Keys, stick and head aim to the robot's wire scale, and when setpoints go out. No devices.
import XCTest
@testable import RexCallCore

final class SteeringTests: XCTestCase {
    func testKeysGiveFractionsOfTheCapsWithLeftPositive() {
        var keys = Steering()
        keys.forward = true; keys.left = true
        XCTAssertEqual(keys.setpoint(speed: 0.5).lin, 0.5)
        XCTAssertEqual(keys.setpoint(speed: 0.5).ang, 0.5)      // Left turns are positive on the robot.
        keys.left = false; keys.right = true; keys.forward = false; keys.back = true
        XCTAssertEqual(keys.setpoint(speed: 1).lin, -1)
        XCTAssertEqual(keys.setpoint(speed: 1).ang, -1)
        keys.forward = true                                   // Forward and back cancel.
        XCTAssertEqual(keys.setpoint(speed: 1).lin, 0)
    }

    func testTheStickHasADeadZoneAndRightIsANegativeTurn() {
        var stick = Steering()
        stick.stick = (0.1, 0.08)
        XCTAssertFalse(stick.active)
        XCTAssertEqual(stick.setpoint(speed: 1).lin, 0)
        stick.stick = (0.5, 0.8)
        XCTAssertEqual(stick.setpoint(speed: 1).lin, 0.8, accuracy: 1e-9)
        XCTAssertEqual(stick.setpoint(speed: 1).ang, -0.5, accuracy: 1e-9)
        stick.forward = true                                  // Keys win over the stick.
        XCTAssertEqual(stick.setpoint(speed: 1).ang, 0)
    }

    func testSetpointsGoOutWhileSteeringThenHalfASecondOfZerosThenNothing() {
        var pacer = SetpointPacer()
        XCTAssertFalse(pacer.shouldSend(lin: 0, ang: 0))      // Never steered: silence.
        XCTAssertTrue(pacer.shouldSend(lin: 0.4, ang: 0))
        let zeros = (0..<8).map { _ in pacer.shouldSend(lin: 0, ang: 0) }
        XCTAssertEqual(zeros, [true, true, true, true, true, false, false, false])
        pacer.releaseAfterStop()
        XCTAssertTrue(pacer.shouldSend(lin: 0, ang: 0))       // The zero that ends a Stop's hold.
    }

    func testHeadAimClampsToEachPartsRangeAndSendsOnlyWhatChanged() {
        var aim = HeadAim(home: ["neck": 0, "pitch": 0.1, "tilt": 0, "lift": 0.35, "visor": 1])
        let before = aim
        aim.nudge("neck", by: 3)
        aim.nudge("lift", by: -2)
        XCTAssertEqual(aim.neck, 1)
        XCTAssertEqual(aim.lift, 0)
        XCTAssertEqual(aim.changes(since: before), ["neck": 1, "lift": 0])
        XCTAssertEqual(aim.changes(since: aim), [:])
        XCTAssertEqual(Set(aim.changes(since: nil).keys), Set(HeadAim.fields))
    }
}
