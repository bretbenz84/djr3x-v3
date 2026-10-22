"""Output-owned finite expressions through RobotServos, never a second wire owner."""
from .attention import AttentionReceipt
from .performance import GestureReceipt
from .contracts import ProviderFailure
from .servos import ServoRequest


class PhysicalAttention:
    medium = 'controller'
    # 'people' looks back at the people here and his eyes follow their faces again (the
    # look_at_people pose). October 22 (1072c10a): beats to "return attention to Brett" invented a
    # face or person id, which no direction is, and the beat failed.
    targets = ('left','right','up','forward','people','unfocused')

    def __init__(self, robot):
        self.robot, self.started = robot, False

    def start(self):
        if self.robot.owner.medium != 'controller' or self.robot.owner.state != 'active':
            raise ProviderFailure('PhysicalAttentionRequiresStartedRobot')
        self.started = True

    def close(self):
        self.started = False   # Robot lifecycle belongs to ProviderOwner.

    def execute(self, intent, cancel):
        cancel.check()
        if not self.started:raise ProviderFailure('PhysicalAttentionNotStarted')
        if intent.anchor is not None:raise ProviderFailure('PhysicalTargetGeometryUnavailable')
        if intent.target == 'people':
            receipt = self.robot.execute_pose('look_at_people',cancel,intent.request_id,hold_pose=False)
        else:
            request = ServoRequest(intent.request_id,self.robot.owner.version,'attention',intent.expires_at,
                                   target=intent.target,hold_seconds=intent.hold_seconds)
            receipt = self.robot.execute_expression(request,cancel)
        cancel.check()
        if receipt.status != 'pulses_reached':raise ProviderFailure(receipt.reason or receipt.status)
        return AttentionReceipt(receipt.request_id,receipt.status,receipt.at,receipt.medium)


class PhysicalGesture(PhysicalAttention):
    targets = ('unfocused',)

    def execute(self, gesture, cancel):
        cancel.check()
        if not self.started:raise ProviderFailure('PhysicalGestureNotStarted')
        if gesture.anchor is not None:raise ProviderFailure('PhysicalTargetGeometryUnavailable')
        request = ServoRequest(gesture.request_id,self.robot.owner.version,'gesture',gesture.expires_at,
                               target=gesture.target,primitive=gesture.primitive,intensity=gesture.intensity)
        receipt = self.robot.execute_expression(request,cancel)
        return GestureReceipt(receipt.request_id,receipt.status,receipt.at,receipt.medium,receipt.reason)
