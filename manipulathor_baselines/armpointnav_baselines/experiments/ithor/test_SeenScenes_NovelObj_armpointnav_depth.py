from ithor_arm.ithor_arm_task_samplers import ArmPointNavTaskSampler
from manipulathor_baselines.armpointnav_baselines.experiments.ithor.armpointnav_depth import (
    ArmPointNavDepth,
)
from manipulathor_baselines.armpointnav_baselines.experiments.ithor.armpointnav_ithor_base import (
    ArmPointNaviThorBaseConfig,
)


class TestOnUObjSSceneArmNavDepth(ArmPointNavDepth):
    """An Object Navigation experiment configuration in iThor with RGB
    input."""

    MAX_STEPS = 200
    TASK_SAMPLER = ArmPointNavTaskSampler
    TEST_SCENES = ArmPointNaviThorBaseConfig.TRAIN_SCENES
    OBJECT_TYPES = ArmPointNaviThorBaseConfig.UNSEEN_OBJECT_TYPES
