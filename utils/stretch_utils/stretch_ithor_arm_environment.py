"""A wrapper for engaging with the ManipulaTHOR environment."""

import copy
import datetime
import typing
import warnings
from typing import Dict, Union, Optional

import ai2thor.server
import numpy as np
from ai2thor.controller import Controller
from allenact_plugins.ithor_plugin.ithor_constants import VISIBILITY_DISTANCE, FOV
from allenact_plugins.ithor_plugin.ithor_environment import IThorEnvironment
from torch.distributions.utils import lazy_property

from ithor_arm.ithor_arm_environment import ManipulaTHOREnvironment
from ithor_arm.ithor_arm_noise_models import NoiseInMotionHabitatFlavor, NoiseInMotionSimple1DNormal

from utils.stretch_utils.stretch_constants import (
    ADITIONAL_ARM_ARGS,
    PICKUP, DONE, MOVE_AHEAD, ROTATE_RIGHT, ROTATE_LEFT, MOVE_BACK, MOVE_ARM_HEIGHT_P, MOVE_ARM_HEIGHT_M, MOVE_ARM_Z_P, MOVE_ARM_Z_M, MOVE_WRIST_P, MOVE_WRIST_M, MOVE_WRIST_P_SMALL, MOVE_WRIST_M_SMALL, ROTATE_LEFT_SMALL, ROTATE_RIGHT_SMALL,
)
from manipulathor_utils.debugger_util import ForkedPdb
from scripts.jupyter_helper import ARM_MOVE_CONSTANT
from scripts.stretch_jupyter_helper import get_relative_stretch_current_arm_state, WRIST_ROTATION, \
    reset_environment_and_additional_commands, AGENT_ROTATION_DEG, AGENT_MOVEMENT_CONSTANT, \
    remove_nan_inf_for_frames, get_reachable_positions
from utils.stretch_utils.stretch_constants import STRETCH_MANIPULATHOR_COMMIT_ID
from utils.stretch_utils.stretch_sim2real_utils import kinect_reshape, intel_reshape


class StretchManipulaTHOREnvironment(ManipulaTHOREnvironment): #TODO this comes at a big big price!
    """Wrapper for the manipulathor controller providing arm functionality
    and bookkeeping.

    See [here](https://ai2thor.allenai.org/documentation/installation) for comprehensive
     documentation on AI2-THOR.

    # Attributes

    controller : The ai2thor controller.
    """
    def __init__(
            self,
            x_display: Optional[str] = None,
            docker_enabled: bool = False,
            local_thor_build: Optional[str] = None,
            visibility_distance: float = VISIBILITY_DISTANCE,
            fov: float = FOV,
            player_screen_width: int = 224,
            player_screen_height: int = 224,
            quality: str = "Very Low",
            restrict_to_initially_reachable_points: bool = False,
            make_agents_visible: bool = True,
            object_open_speed: float = 1.0,
            simplify_physics: bool = False,
            verbose: bool = False,
            env_args=None,
    ) -> None:
        """Initializer.

        # Parameters

        x_display : The x display into which to launch ai2thor (possibly necessarily if you are running on a server
            without an attached display).
        docker_enabled : Whether or not to run thor in a docker container (useful on a server without an attached
            display so that you don't have to start an x display).
        local_thor_build : The path to a local build of ai2thor. This is probably not necessary for your use case
            and can be safely ignored.
        visibility_distance : The distance (in meters) at which objects, in the viewport of the agent,
            are considered visible by ai2thor and will have their "visible" flag be set to `True` in the metadata.
        fov : The agent's camera's field of view.
        width : The width resolution (in pixels) of the images returned by ai2thor.
        height : The height resolution (in pixels) of the images returned by ai2thor.
        quality : The quality at which to render. Possible quality settings can be found in
            `ai2thor._quality_settings.QUALITY_SETTINGS`.
        restrict_to_initially_reachable_points : Whether or not to restrict the agent to locations in ai2thor
            that were found to be (initially) reachable by the agent (i.e. reachable by the agent after resetting
            the scene). This can be useful if you want to ensure there are only a fixed set of locations where the
            agent can go.
        make_agents_visible : Whether or not the agent should be visible. Most noticable when there are multiple agents
            or when quality settings are high so that the agent casts a shadow.
        object_open_speed : How quickly objects should be opened. High speeds mean faster simulation but also mean
            that opening objects have a lot of kinetic energy and can, possibly, knock other objects away.
        simplify_physics : Whether or not to simplify physics when applicable. Currently this only simplies object
            interactions when opening drawers (when simplified, objects within a drawer do not slide around on
            their own when the drawer is opened or closed, instead they are effectively glued down).
        """

        self._start_player_screen_width = player_screen_width
        self._start_player_screen_height = player_screen_height
        self._local_thor_build = local_thor_build
        self.x_display = x_display
        # self.controller: Optional[Controller] = None
        self._started = False
        self._quality = quality
        self._verbose = verbose
        self.env_args = env_args

        self._initially_reachable_points: Optional[typing.List[Dict]] = None
        self._initially_reachable_points_set: Optional[typing.Set[typing.Tuple[float, float]]] = None
        self._move_mag: Optional[float] = None
        self._grid_size: Optional[float] = None
        self._visibility_distance = visibility_distance

        self._fov = fov

        self.restrict_to_initially_reachable_points = (
            restrict_to_initially_reachable_points
        )
        self.make_agents_visible = make_agents_visible
        self.object_open_speed = object_open_speed
        self._always_return_visible_range = False
        self.simplify_physics = simplify_physics

        if 'motion_noise_type' in env_args.keys():
            if env_args['motion_noise_type'] == 'habitat':
                self.noise_model = NoiseInMotionHabitatFlavor(**env_args['motion_noise_args'])
            elif env_args['motion_noise_type'] == 'simple1d':
                self.noise_model = NoiseInMotionSimple1DNormal(**env_args['motion_noise_args'])
            else:
                print('Unrecognized motion noise model type')
                ForkedPdb().set_trace()
        else:
            self.noise_model = NoiseInMotionHabitatFlavor(effect_scale=0.0) # un-noise model

        self.ahead_nominal = AGENT_MOVEMENT_CONSTANT
        self.rotate_nominal = AGENT_ROTATION_DEG

        # self.start(None)


        # noinspection PyTypeHints
        self.docker_enabled = docker_enabled


        self.MEMORY_SIZE = 5
        # self.memory_frames = []


        if "quality" not in self.env_args:
            self.env_args["quality"] = self._quality

        # directory_to_save = "experiment_output/logging_debugging"
        # import os
        # os.makedirs(directory_to_save, exist_ok=True)
        # timestamp = datetime.datetime.now().strftime("%Y_%m_%d_%H_%M_%S_%f.txt")

    def get_reachable_positions(self):
        return get_reachable_positions(self.controller)
    def start(
            self,
            scene_name: Optional[str],
            move_mag: float = 0.25,
            **kwargs,
    ) -> None:

        raise Exception('SHOULD NOT CALL')
        # """Starts the ai2thor controller if it was previously stopped.
        #
        # After starting, `reset` will be called with the scene name and move magnitude.
        #
        # # Parameters
        #
        # scene_name : The scene to load.
        # move_mag : The amount of distance the agent moves in a single `MoveAhead` step.
        # kwargs : additional kwargs, passed to reset.
        # """
        # if self._started:
        #     raise RuntimeError(
        #         "Trying to start the environment but it is already started."
        #     )
        #
        # self.controller = self.create_controller()
        #
        # if (
        #         self._start_player_screen_height,
        #         self._start_player_screen_width,
        # ) != self.current_frame.shape[:2]:
        #     self.controller.step(
        #         {
        #             "action": "ChangeResolution",
        #             "x": self._start_player_screen_width,
        #             "y": self._start_player_screen_height,
        #         }
        #     )
        #
        # self._started = True
        # self.reset(scene_name=scene_name, move_mag=move_mag, **kwargs)
    def reset(
            self,
            scene_name: Optional[str],
            move_mag: float = 0.25,
            **kwargs,
    ):
        self._move_mag = move_mag
        self._grid_size = self._move_mag
        # self.memory_frames = []

        if scene_name is None:
            scene_name = self.controller.last_event.metadata["sceneName"]
        # self.reset_init_params()#**kwargs) removing this fixes one of the crashing problem

        # to solve the crash issue
        # why do we still have this crashing problem?
        try:
            reset_environment_and_additional_commands(self.controller, scene_name)
        except Exception as e:
            print("RESETTING THE SCENE,", scene_name, 'because of', str(e))
            self.controller = self.create_controller()
            reset_environment_and_additional_commands(self.controller, scene_name)

        if self.object_open_speed != 1.0:
            self.controller.step(
                {"action": "ChangeOpenSpeed", "x": self.object_open_speed}
            )

        self._initially_reachable_points = None
        self._initially_reachable_points_set = None
        self.controller.step({"action": "GetReachablePositions"})
        if not self.controller.last_event.metadata["lastActionSuccess"]:
            warnings.warn(
                "Error when getting reachable points: {}".format(
                    self.controller.last_event.metadata["errorMessage"]
                )
            )
        self._initially_reachable_points = self.last_action_return

        self.list_of_actions_so_far = []

        self.noise_model.reset_noise_model()
        self.nominal_agent_location = self.get_agent_location()


    def check_controller_version(self, controller=None):
        controller_to_check = controller
        if controller_to_check is None:
            controller_to_check = self.controller
        if STRETCH_MANIPULATHOR_COMMIT_ID is not None:
            assert (
                self.env_args['commit_id'] in controller_to_check._build.url
            ), "Build number is not right, {} vs {}, use  pip3 install -e git+https://github.com/allenai/ai2thor.git@{}#egg=ai2thor".format(
                controller_to_check._build.url,
                STRETCH_MANIPULATHOR_COMMIT_ID,
                STRETCH_MANIPULATHOR_COMMIT_ID,
            )

    def create_controller(self):
        assert 'commit_id' in self.env_args
        controller = Controller(**self.env_args)#, commit_id=STRETCH_MANIPULATHOR_COMMIT_ID)
        return controller

    @lazy_property
    def controller(self):
        self._started = True
        controller = self.create_controller()
        self.check_controller_version(controller)
        controller.docker_enabled = self.docker_enabled  # type: ignore
        return controller

    @property
    def kinect_frame(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        frame = self.controller.last_event.third_party_camera_frames[0].copy()
        frame = remove_nan_inf_for_frames(frame, 'kinect_frame')
        return kinect_reshape(frame)

    @property
    def kinect_frame_no_reshape(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        frame = self.controller.last_event.third_party_camera_frames[0].copy()
        frame = remove_nan_inf_for_frames(frame, 'kinect_frame')
        return frame
    
    @property
    def kinect_depth(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        depth_frame = self.controller.last_event.third_party_depth_frames[0].copy()
        depth_frame = remove_nan_inf_for_frames(depth_frame, 'depth_kinect')

        # #TODO remove
        if np.sum(depth_frame != self.controller.last_event.third_party_depth_frames[0].copy()) > 10:
            ForkedPdb().set_trace()

        return kinect_reshape(depth_frame)

    
    @property
    def kinect_depth_no_reshape(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        depth_frame = self.controller.last_event.third_party_depth_frames[0].copy()
        depth_frame = remove_nan_inf_for_frames(depth_frame, 'depth_kinect')
        return depth_frame

    @property
    def intel_frame(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        frame = self.controller.last_event.frame.copy()
        frame = remove_nan_inf_for_frames(frame, 'intel_frame')
        return intel_reshape(frame)

    @property
    def intel_frame_no_reshape(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        frame = self.controller.last_event.frame.copy()
        frame = remove_nan_inf_for_frames(frame, 'intel_frame')
        return frame
    
    @property
    def intel_depth(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        depth_frame = self.controller.last_event.depth_frame.copy()
        depth_frame = remove_nan_inf_for_frames(depth_frame, 'depth_intel')
        return intel_reshape(depth_frame)

    @property
    def intel_depth_no_reshape(self) -> np.ndarray:
        """Returns rgb image corresponding to the agent's egocentric view."""
        depth_frame = self.controller.last_event.depth_frame.copy()
        depth_frame = remove_nan_inf_for_frames(depth_frame, 'depth_intel')
        return depth_frame

    def get_current_arm_state(self):
        ForkedPdb().set_trace()


    def get_absolute_hand_state(self):
        event = self.controller.last_event
        joints = event.metadata["arm"]["joints"]
        arm = copy.deepcopy(joints[-1])
        # xyz_dict = arm["position"]
        # xyz_dict = get_relative_stretch_current_arm_state(self.controller)
        return dict(position=arm['position'], rotation={"x": 0, "y": 0, "z": 0})

    def get_pickupable_objects(self):

        event = self.controller.last_event
        object_list = event.metadata["arm"]["pickupableObjects"]

        return object_list
    
    def update_nominal_location(self, action_dict):
        # location = {
        #     "x": metadata["agent"]["position"]["x"],
        #     "y": metadata["agent"]["position"]["y"],
        #     "z": metadata["agent"]["position"]["z"],
        #     "rotation": metadata["agent"]["rotation"]["y"],
        #     "horizon": metadata["agent"]["cameraHorizon"],
        #     "standing": metadata.get("isStanding", metadata["agent"].get("isStanding")),
        # }

        curr_loc = self.nominal_agent_location
        new_loc = copy.deepcopy(curr_loc)

        if action_dict['action'] is 'RotateLeft':
            new_loc["rotation"] = (new_loc["rotation"] - self.rotate_nominal) % 360
        if action_dict['action'] is 'RotateLeftSmall':
            new_loc["rotation"] = (new_loc["rotation"] - self.rotate_nominal/5) % 360
        if action_dict['action'] is 'RotateRight':
            new_loc["rotation"] = (new_loc["rotation"] + self.rotate_nominal) % 360
        elif action_dict['action'] is 'RotateRightSmall':
            new_loc["rotation"] = (new_loc["rotation"] + self.rotate_nominal/5) % 360
        elif action_dict['action'] is 'MoveAhead':
            new_loc["x"] += self.ahead_nominal * np.sin(new_loc["rotation"] * np.pi / 180)
            new_loc["z"] += self.ahead_nominal * np.cos(new_loc["rotation"] * np.pi / 180)
        elif action_dict['action'] is 'MoveBack':
            new_loc["x"] -= self.ahead_nominal * np.sin(new_loc["rotation"] * np.pi / 180)
            new_loc["z"] -= self.ahead_nominal * np.cos(new_loc["rotation"] * np.pi / 180)
        elif action_dict['action'] is 'TeleportFull':
            new_loc["x"] = action_dict['x']
            new_loc["y"] = action_dict['y']
            new_loc["z"] = action_dict['z']
            new_loc["rotation"] = action_dict['rotation']['y']
            new_loc["horizon"] = action_dict['horizon']

        self.nominal_agent_location = new_loc

    def step(
        self, action_dict: Dict[str, Union[str, int, float]]
    ) -> ai2thor.server.Event:
        """Take a step in the ai2thor environment."""

        action = typing.cast(str, action_dict["action"])
        original_action_dict = copy.deepcopy(action_dict)

        skip_render = "renderImage" in action_dict and not action_dict["renderImage"]
        last_frame: Optional[np.ndarray] = None
        if skip_render:
            last_frame = self.current_frame

        if self.simplify_physics:
            action_dict["simplifyOPhysics"] = True
        if action in [PICKUP, DONE]:
            if action == PICKUP:
                object_id = action_dict["object_id"]
                if not self.is_object_at_low_level_hand(object_id):
                    pickupable_objects = self.get_pickupable_objects()
                    #
                    if object_id in pickupable_objects:
                        # This version of the task is actually harder # consider making it easier, are we penalizing failed pickup? yes
                        event = self.step(dict(action="PickupObject"))
                        #  we are doing an additional pass here, label is not right and if we fail we will do it twice
                        object_inventory = self.controller.last_event.metadata["arm"][
                            "heldObjects"
                        ]
                        if (
                            len(object_inventory) > 0
                            and object_id not in object_inventory
                        ):
                            print('Picked up the wrong object')
                            event = self.step(dict(action="ReleaseObject"))
            action_dict = {
                'action': 'Pass'
            } # we have to change the last action success if the pik up fails, we do that in the task now
                
        elif action in [MOVE_AHEAD, MOVE_BACK, ROTATE_RIGHT, ROTATE_LEFT, ROTATE_RIGHT_SMALL, ROTATE_LEFT_SMALL]:
            copy_aditions = copy.deepcopy(ADITIONAL_ARM_ARGS)

            # RH: order matters, nominal action happens last
            action_dict = {**action_dict, **copy_aditions}
            # ForkedPdb().set_trace()
            if action in [MOVE_AHEAD]:
                noise = self.noise_model.get_ahead_drift(self.ahead_nominal)

                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2]
                sr = self.controller.step(action_dict)

                action_dict = {**copy_aditions}
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0] + self.ahead_nominal
                action_dict["right"] = noise[1]

            elif action in [MOVE_BACK]:
                noise = self.noise_model.get_ahead_drift(self.ahead_nominal)
                # TODO revisit - make sense to sample the same for ahead and back? 
                # inclination is effect matters less than currently unmodeled hysteresis effects

                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2]
                sr = self.controller.step(action_dict)

                action_dict = {**copy_aditions}
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0] - self.ahead_nominal
                action_dict["right"] = noise[1]


            elif action in [ROTATE_RIGHT]:
                noise = self.noise_model.get_rotate_drift()
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0]
                action_dict["right"] = noise[1]
                sr = self.controller.step(action_dict)

                action_dict = {**copy_aditions}
                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2] + self.rotate_nominal

            elif action in [ROTATE_LEFT]:
                noise = self.noise_model.get_rotate_drift()
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0]
                action_dict["right"] = noise[1]
                sr = self.controller.step(action_dict)

                action_dict = {**copy_aditions}
                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2] - self.rotate_nominal
            
            elif action in [ROTATE_RIGHT_SMALL]:
                # RH: lesser scaling noise is deliberate. Small actions are harder to be accurate
                noise = self.noise_model.get_rotate_drift()
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0]/2
                action_dict["right"] = noise[1]/2
                sr = self.controller.step(action_dict)

                action_dict = {**copy_aditions}
                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2]/2 + self.rotate_nominal / 5

            elif action in [ROTATE_LEFT_SMALL]:
                # RH: lesser scaling noise is deliberate. Small actions are harder to be accurate
                noise = self.noise_model.get_rotate_drift()
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0]/2
                action_dict["right"] = noise[1]/2
                sr = self.controller.step(action_dict)

                action_dict = {**copy_aditions}
                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2]/2 - self.rotate_nominal / 5


        elif action in [MOVE_ARM_HEIGHT_P,MOVE_ARM_HEIGHT_M,MOVE_ARM_Z_P,MOVE_ARM_Z_M,]:
            base_position = get_relative_stretch_current_arm_state(self.controller)
            change_value = ARM_MOVE_CONSTANT
            if action == MOVE_ARM_HEIGHT_P:
                base_position['y'] += change_value
            elif action == MOVE_ARM_HEIGHT_M:
                base_position['y'] -= change_value
            elif action == MOVE_ARM_Z_P:
                base_position['z'] += change_value
            elif action == MOVE_ARM_Z_M:
                base_position['z'] -= change_value
            action_dict = dict(action='MoveArm', position=dict(x=base_position['x'], y=base_position['y'], z=base_position['z']),**ADITIONAL_ARM_ARGS)
        elif action in [MOVE_WRIST_P,MOVE_WRIST_M,]:
            if action == MOVE_WRIST_P:
                action_dict = dict(action='RotateWristRelative', yaw=-WRIST_ROTATION)
            elif action == MOVE_WRIST_M:
                action_dict = dict(action='RotateWristRelative', yaw=WRIST_ROTATION)
        elif action == MOVE_WRIST_P_SMALL:
            action_dict = dict(action='RotateWristRelative', yaw=-WRIST_ROTATION / 5)
        elif action == MOVE_WRIST_M_SMALL:
            action_dict = dict(action='RotateWristRelative', yaw=WRIST_ROTATION / 5)


        sr_nominal = self.controller.step(action_dict)
        self.list_of_actions_so_far.append(action_dict)

        # RH: Nominal location only updates for successful actions. Note that that drift 
        # action might succeed even if the "main" action fails
        if sr_nominal.metadata["lastActionSuccess"]:
            self.update_nominal_location(original_action_dict)

        if self._verbose:
            print(self.controller.last_event)

        if self.restrict_to_initially_reachable_points:
            self._snap_agent_to_initially_reachable()

        if skip_render:
            assert last_frame is not None
            self.last_event.frame = last_frame

        if not sr_nominal.metadata["lastActionSuccess"] and action in [MOVE_AHEAD, MOVE_BACK, ROTATE_RIGHT, ROTATE_LEFT, ROTATE_RIGHT_SMALL, ROTATE_LEFT_SMALL]:
            # if the action fails, sample the noise model for a turn 
            # does this mess up metadata? and is this reasonable? what action failure modes happen in sim vs real?
            noise = self.noise_model.get_rotate_drift()
            action_dict = {**copy.deepcopy(ADITIONAL_ARM_ARGS)}
            action_dict["action"] = "MoveAgent"
            action_dict["ahead"] = noise[0]
            action_dict["right"] = noise[1]
            sr = self.controller.step(action_dict)
            action_dict = {**copy.deepcopy(ADITIONAL_ARM_ARGS)}
            action_dict["action"] = "RotateAgent"
            action_dict["degrees"] = noise[2]
            sr = self.controller.step(action_dict)

        return sr_nominal
