import platform

import gym
from allenact_plugins.ithor_plugin.ithor_sensors import RGBSensorThor
from torch import nn

from ithor_arm.bring_object_sensors import CategorySampleSensor, NoisyObjectMask, NoGripperRGBSensorThor
from ithor_arm.bring_object_task_samplers import DiverseBringObjectTaskSampler
from ithor_arm.ithor_arm_constants import ENV_ARGS, TRAIN_OBJECTS, TEST_OBJECTS
from ithor_arm.ithor_arm_sensors import (
    InitialAgentArmToObjectSensor,
    InitialObjectToGoalSensor,
    PickedUpObjSensor,
    DepthSensorThor, RelativeAgentArmToObjectSensor, RelativeObjectToGoalSensor,
)
from ithor_arm.ithor_arm_viz import MaskImageVisualizer
from manipulathor_baselines.bring_object_baselines.experiments.bring_object_mixin_ddppo import BringObjectMixInPPOConfig
from manipulathor_baselines.bring_object_baselines.experiments.bring_object_mixin_simplegru import BringObjectMixInSimpleGRUConfig
from manipulathor_baselines.bring_object_baselines.experiments.ithor.bring_object_ithor_base import BringObjectiThorBaseConfig
from manipulathor_baselines.bring_object_baselines.models.pickup_object_with_mask_model import PickUpWMaskBaselineActorCritic
from manipulathor_baselines.bring_object_baselines.models.predict_mask_small_bring_object_model import SmallBringObjectWPredictMaskDepthBaselineActorCritic
from manipulathor_baselines.bring_object_baselines.models.query_obj_w_gt_mask_rgb_model import SmallBringObjectWQueryObjGtMaskRGBDModel
from manipulathor_baselines.bring_object_baselines.models.query_obj_w_gt_mask_small_bring_object_model import SmallBringObjectWQueryObjGtMaskDepthBaselineActorCritic
from manipulathor_baselines.bring_object_baselines.models.rgbd_with_noise_recovery_model import RGBDBringObjectWNoiseRecoveryModel
from manipulathor_baselines.bring_object_baselines.models.small_bring_object_with_mask_model import SmallBringObjectWMaskDepthBaselineActorCritic
from manipulathor_baselines.bring_object_baselines.models.small_depth_pickup_object_with_mask_model import SmallPickUpWMaskDepthBaselineActorCritic


class RGBDModelWNoiseRecovery(
    BringObjectiThorBaseConfig,
    BringObjectMixInPPOConfig,
    BringObjectMixInSimpleGRUConfig,
):
    """An Object Navigation experiment configuration in iThor with RGB
    input."""
    NOISE_LEVEL = 0.2
    SENSORS = [
        # NoGripperRGBSensorThor(
        #     height=BringObjectiThorBaseConfig.SCREEN_SIZE,
        #     width=BringObjectiThorBaseConfig.SCREEN_SIZE,
        #     use_resnet_normalization=True,
        #     uuid="only_detection_rgb_lowres",
        # ),
        RGBSensorThor(
            height=BringObjectiThorBaseConfig.SCREEN_SIZE,
            width=BringObjectiThorBaseConfig.SCREEN_SIZE,
            use_resnet_normalization=True,
            uuid="rgb_lowres",
        ),
        DepthSensorThor(
            height=BringObjectiThorBaseConfig.SCREEN_SIZE,
            width=BringObjectiThorBaseConfig.SCREEN_SIZE,
            use_normalization=True,
            uuid="depth_lowres",
        ),
        PickedUpObjSensor(),
        CategorySampleSensor(type='source'),
        CategorySampleSensor(type='destination'),
        NoisyObjectMask(noise=NOISE_LEVEL, type='source'),
        NoisyObjectMask(noise=NOISE_LEVEL, type='destination'),
    ]

    MAX_STEPS = 200

    # POTENTIAL_VISUALIZERS = BringObjectiThorBaseConfig.POTENTIAL_VISUALIZERS + [MaskImageVisualizer]

    if platform.system() == "Darwin":
        MAX_STEPS = 200#3


    TASK_SAMPLER = DiverseBringObjectTaskSampler
    NUM_PROCESSES = 40

    OBJECT_TYPES = TRAIN_OBJECTS + TEST_OBJECTS


    @classmethod
    def create_model(cls, **kwargs) -> nn.Module:
        #TODO initialize the weights that are shared with the pretrained version?
        return RGBDBringObjectWNoiseRecoveryModel(
            action_space=gym.spaces.Discrete(
                len(cls.TASK_SAMPLER._TASK_TYPE.class_action_names())
            ),
            observation_space=kwargs["sensor_preprocessor_graph"].observation_spaces,
            hidden_size=512,
        )

    @classmethod
    def tag(cls):
        return cls.__name__
