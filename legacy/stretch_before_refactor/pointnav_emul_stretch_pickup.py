import platform

import gym
import torch
from torch import nn

from ithor_arm.bring_object_sensors import NoisyObjectMask
from ithor_arm.ithor_arm_constants import TRAIN_OBJECTS, TEST_OBJECTS
from ithor_arm.ithor_arm_sensors import (
    PickedUpObjSensor,
)
from ithor_arm.ithor_arm_viz import TestMetricLogger
from ithor_arm.near_deadline_sensors import RealPointNavSensor
from manipulathor_baselines.bring_object_baselines.experiments.bring_object_mixin_ddppo import BringObjectMixInPPOConfig
from manipulathor_baselines.bring_object_baselines.experiments.bring_object_mixin_simplegru import BringObjectMixInSimpleGRUConfig
from manipulathor_baselines.bring_object_baselines.experiments.ithor.bring_object_ithor_base import BringObjectiThorBaseConfig
from manipulathor_baselines.stretch_bring_object_baselines.models.stretch_pointnav_emul_model import StretchPointNavEmulModel
from manipulathor_baselines.stretch_bring_object_baselines.models.stretch_real_pointnav_model import StretchRealPointNavModel
from utils.stretch_utils.stretch_bring_object_task_samplers import StretchDiverseBringObjectTaskSampler
from utils.stretch_utils.stretch_bring_object_tasks import StretchExploreWiseRewardTask, \
    StretchExploreWiseRewardTaskOnlyPickUp, StretchObjectNavTask
from utils.stretch_utils.stretch_thor_sensors import RGBSensorStretchIntel, DepthSensorStretchIntel, \
    RGBSensorStretchKinect, DepthSensorStretchKinect, AgentBodyPointNavSensor, AgentBodyPointNavEmulSensor, \
    RGBSensorStretchKinectZero, \
    DepthSensorStretchKinectZero, IntelRawDepthSensor, ArmPointNavEmulSensor, KinectRawDepthSensor, \
    KinectNoisyObjectMask, IntelNoisyObjectMask
from utils.stretch_utils.stretch_visualizer import StretchBringObjImageVisualizer


class PointNavEmulStretchPickUp(
    BringObjectiThorBaseConfig,
    BringObjectMixInPPOConfig,
    BringObjectMixInSimpleGRUConfig,
):
    """An Object Navigation experiment configuration in iThor with RGB
    input."""
    desired_screen_size = 224
    NOISE_LEVEL = 0
    distance_thr = 1.5 # is this a good number?

    source_mask_sensor_intel = IntelNoisyObjectMask(height=BringObjectiThorBaseConfig.SCREEN_SIZE, width=BringObjectiThorBaseConfig.SCREEN_SIZE,noise=0, type='source', distance_thr=distance_thr)
    destination_mask_sensor_intel = IntelNoisyObjectMask(height=BringObjectiThorBaseConfig.SCREEN_SIZE, width=BringObjectiThorBaseConfig.SCREEN_SIZE,noise=0, type='destination', distance_thr=distance_thr)
    depth_sensor_intel = IntelRawDepthSensor()

    source_mask_sensor_kinect = KinectNoisyObjectMask(height=BringObjectiThorBaseConfig.SCREEN_SIZE, width=BringObjectiThorBaseConfig.SCREEN_SIZE,noise=0, type='source', distance_thr=distance_thr)
    destination_mask_sensor_kinect = KinectNoisyObjectMask(height=BringObjectiThorBaseConfig.SCREEN_SIZE, width=BringObjectiThorBaseConfig.SCREEN_SIZE,noise=0, type='destination', distance_thr=distance_thr)
    depth_sensor_kinect = KinectRawDepthSensor()

    # source_object_category_sensor = TempObjectCategorySensor(type='source')
    # destination_object_category_sensor = TempObjectCategorySensor(type='destination')
    # category_sample_source = CategorySampleSensor(type='source')
    # category_sample_destination = CategorySampleSensor(type='destination')
    # rgb_for_detection_sensor = NoGripperRGBSensorThor(
    #     height=BringObjectiThorBaseConfig.SCREEN_SIZE,
    #     width=BringObjectiThorBaseConfig.SCREEN_SIZE,
    #     use_resnet_normalization=True,
    #     uuid="only_detection_rgb_lowres",
    # ) # TODO add this for the arm

    # object_mask_source = ObjectRelativeAgentCoordinateSensor(type='source')
    # object_mask_destination = ObjectRelativeAgentCoordinateSensor(type='destination')
    # agent_loc_sensor = AgentGTLocationSensor()

    SENSORS = [
        RGBSensorStretchIntel(
            height=desired_screen_size,
            width=desired_screen_size,
            use_resnet_normalization=True,
            uuid="rgb_lowres",
        ),
        DepthSensorStretchIntel(height=desired_screen_size,width=desired_screen_size,use_normalization=True,uuid="depth_lowres",),
        RGBSensorStretchKinect(
            height=desired_screen_size,
            width=desired_screen_size,
            use_resnet_normalization=True,
            uuid="rgb_lowres_arm",
        ),
        DepthSensorStretchKinect(
            height=desired_screen_size,
            width=desired_screen_size,
            use_normalization=True,
            uuid="depth_lowres_arm",
        ),
        PickedUpObjSensor(),
        # AgentBodyPointNavEmulSensor(type='source', agent_location_sensor=agent_loc_sensor, object_mask_sensor_intel=object_mask_source),
        # AgentBodyPointNavEmulSensor(type='destination', agent_location_sensor=agent_loc_sensor, object_mask_sensor_intel=object_mask_destination),
        AgentBodyPointNavEmulSensor(type='source', mask_sensor=source_mask_sensor_intel, depth_sensor=depth_sensor_intel),
        AgentBodyPointNavEmulSensor(type='destination', mask_sensor=destination_mask_sensor_intel, depth_sensor=depth_sensor_intel),
        ArmPointNavEmulSensor(type='source', mask_sensor=source_mask_sensor_kinect, depth_sensor=depth_sensor_kinect),
        ArmPointNavEmulSensor(type='destination', mask_sensor=destination_mask_sensor_kinect, depth_sensor=depth_sensor_kinect),
        source_mask_sensor_intel,
        destination_mask_sensor_intel,
        source_mask_sensor_kinect,
        destination_mask_sensor_kinect,

    ]

    MAX_STEPS = 200

    TASK_SAMPLER = StretchDiverseBringObjectTaskSampler
    TASK_TYPE = StretchExploreWiseRewardTaskOnlyPickUp # TODO change

    NUM_PROCESSES = 40

    OBJECT_TYPES = TRAIN_OBJECTS + TEST_OBJECTS

    POTENTIAL_VISUALIZERS = [StretchBringObjImageVisualizer, TestMetricLogger]

    # if platform.system() == "Darwin":
    #     MAX_STEPS = 200

    def __init__(self):
        super().__init__()
        self.REWARD_CONFIG['exploration_reward'] = 0.1
        self.REWARD_CONFIG['object_found'] = 1
        self.ENV_ARGS['visibilityDistance'] = self.distance_thr
        self.ENV_ARGS['renderInstanceSegmentation'] = True

    def test_task_sampler_args(self, **kwargs):
        sampler_args = super(PointNavEmulStretchPickUp, self).test_task_sampler_args(**kwargs)
        if platform.system() == "Darwin":
            pass
        else:
            for sensor_type in sampler_args['sensors']:
                if isinstance(sensor_type, AgentBodyPointNavEmulSensor):
                    sensor_type.device = torch.device(kwargs["devices"][0])

            for sensor_type in sampler_args['sensors']:
                if isinstance(sensor_type, ArmPointNavEmulSensor):
                    sensor_type.device = torch.device(kwargs["devices"][0])

        return sampler_args

    def train_task_sampler_args(self, **kwargs):
        sampler_args = super(PointNavEmulStretchPickUp, self).train_task_sampler_args(**kwargs)
        if platform.system() == "Darwin":
            pass
        else:

            for sensor_type in sampler_args['sensors']:
                if isinstance(sensor_type, AgentBodyPointNavEmulSensor):
                    sensor_type.device = torch.device(kwargs["devices"][0])
            for sensor_type in sampler_args['sensors']:
                if isinstance(sensor_type, ArmPointNavEmulSensor):
                    sensor_type.device = torch.device(kwargs["devices"][0])
        return sampler_args


    @classmethod
    def create_model(cls, **kwargs) -> nn.Module:
        return StretchPointNavEmulModel(
            action_space=gym.spaces.Discrete(
                len(cls.TASK_TYPE.class_action_names())
            ),
            observation_space=kwargs["sensor_preprocessor_graph"].observation_spaces,
            hidden_size=512,
            visualize=cls.VISUALIZE
        )

    @classmethod
    def tag(cls):
        return cls.__name__
