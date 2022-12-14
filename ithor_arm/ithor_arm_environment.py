"""A wrapper for engaging with the ManipulaTHOR environment."""

from ast import For
import copy
import math
import typing
import warnings
from typing import Tuple, Dict, List, Set, Union, Any, Optional

import ai2thor.server
from cv2 import USAGE_DEFAULT
import numpy as np
from ai2thor.controller import Controller
from allenact_plugins.ithor_plugin.ithor_constants import VISIBILITY_DISTANCE, FOV
from allenact_plugins.ithor_plugin.ithor_environment import IThorEnvironment

from ithor_arm.ithor_arm_noise_models import NoiseInMotionHabitatFlavor, NoiseInMotionSimple1DNormal
from ithor_arm.arm_calculation_utils import convert_world_to_agent_coordinate

from ithor_arm.ithor_arm_constants import (
    ADITIONAL_ARM_ARGS,
    ARM_MIN_HEIGHT,
    ARM_MAX_HEIGHT,
    MOVE_ARM_HEIGHT_CONSTANT,
    MOVE_ARM_CONSTANT,
    MANIPULATHOR_COMMIT_ID,
    reset_environment_and_additional_commands,
    MOVE_THR, PICKUP, DONE, MOVE_AHEAD, ROTATE_RIGHT, ROTATE_LEFT, MOVE_ARM_HEIGHT_P, MOVE_ARM_HEIGHT_M, MOVE_ARM_X_P, MOVE_ARM_X_M, MOVE_ARM_Y_P, MOVE_ARM_Y_M, MOVE_ARM_Z_P, MOVE_ARM_Z_M, SET_OF_ALL_AGENT_ACTIONS,
)
from manipulathor_utils.debugger_util import ForkedPdb
from scripts.hacky_objects_that_move import OBJECTS_MOVE_THR
from scripts.jupyter_helper import get_reachable_positions


class ManipulaTHOREnvironment(IThorEnvironment):
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
        *_noise_meta_dist_params : [mean, variance] defines the normal distribution over which the actual noise parameters
            for a motion noise distribution will be drawn. Distributions for noise in motion will be re-rolled every scene 
            reset with new bias and variance values drawn from these meta-distributions.
        """

        self._start_player_screen_width = player_screen_width
        self._start_player_screen_height = player_screen_height
        self._local_thor_build = local_thor_build
        self.x_display = x_display
        self.controller: Optional[Controller] = None
        self._started = False
        self._quality = quality
        self._verbose = verbose
        self.env_args = env_args

        self._initially_reachable_points: Optional[List[Dict]] = None
        self._initially_reachable_points_set: Optional[Set[Tuple[float, float]]] = None
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

        self.ahead_nominal = 0.2
        self.rotate_nominal = 45


        self.start(None)
        # self.check_controller_version()

        # noinspection PyTypeHints
        self.controller.docker_enabled = docker_enabled  # type: ignore

        self.MEMORY_SIZE = 5

        if "quality" not in self.env_args:
            self.env_args["quality"] = self._quality
        # self.memory_frames = []

    def check_controller_version(self):
        if MANIPULATHOR_COMMIT_ID is not None:
            assert (
                    MANIPULATHOR_COMMIT_ID in self.controller._build.url
            ), "Build number is not right, {} vs {}, use  pip3 install -e git+https://github.com/allenai/ai2thor.git@{}#egg=ai2thor".format(
                self.controller._build.url,
                MANIPULATHOR_COMMIT_ID,
                MANIPULATHOR_COMMIT_ID,
            )

    # def check_controller_version(self):
    #     if MANIPULATHOR_COMMIT_ID is not None:
    #         assert (
    #                 MANIPULATHOR_COMMIT_ID in self.controller._build.url
    #         ), "Build number is not right, {} vs {}, use  pip3 install -e git+https://github.com/allenai/ai2thor.git@{}#egg=ai2thor".format(
    #             self.controller._build.url,
    #             MANIPULATHOR_COMMIT_ID,
    #             MANIPULATHOR_COMMIT_ID,
    #         )
    def get_reachable_positions(self):
        return get_reachable_positions(self.controller)
    def create_controller(self):
        assert 'commit_id' in self.env_args, 'No commit id is specified'
        controller = Controller(**self.env_args)
        return controller

    def start(
            self,
            scene_name: Optional[str],
            move_mag: float = 0.25,
            **kwargs,
    ) -> None:
        """Starts the ai2thor controller if it was previously stopped.

        After starting, `reset` will be called with the scene name and move magnitude.

        # Parameters

        scene_name : The scene to load.
        move_mag : The amount of distance the agent moves in a single `MoveAhead` step.
        kwargs : additional kwargs, passed to reset.
        """
        if self._started:
            raise RuntimeError(
                "Trying to start the environment but it is already started."
            )

        self.controller = self.create_controller()

        if (
                self._start_player_screen_height,
                self._start_player_screen_width,
        ) != self.current_frame.shape[:2]:
            self.controller.step(
                {
                    "action": "ChangeResolution",
                    "x": self._start_player_screen_width,
                    "y": self._start_player_screen_height,
                }
            )

        self._started = True
        self.reset(scene_name=scene_name, move_mag=move_mag, **kwargs)
    def reset_environment_and_additional_commands(self, scene_name):
        reset_environment_and_additional_commands(self.controller, scene_name)
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
            self.reset_environment_and_additional_commands(scene_name)
        except Exception as e:
            print("RESETTING THE SCENE,", scene_name, 'because of', str(e))
            self.controller = ai2thor.controller.Controller(
                **self.env_args
            )
            self.reset_environment_and_additional_commands(scene_name)

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


    def randomize_agent_location(
            self, seed: int = None, partial_position: Optional[Dict[str, float]] = None
    ) -> Dict:
        raise Exception("not used")

    def is_object_at_low_level_hand(self, object_id):
        current_objects_in_hand = self.controller.last_event.metadata["arm"]["heldObjects"]
        return object_id in current_objects_in_hand

    def object_in_hand(self):
        """Object metadata for the object in the agent's hand."""
        inv_objs = self.last_event.metadata["inventoryObjects"]
        if len(inv_objs) == 0:
            return None
        elif len(inv_objs) == 1:
            return self.get_object_by_id(
                self.last_event.metadata["inventoryObjects"][0]["objectId"]
            )
        else:
            raise AttributeError("Must be <= 1 inventory objects.")

    def correct_nan_inf(self, flawed_dict, extra_tag=""):
        corrected_dict = copy.deepcopy(flawed_dict)
        anything_changed = 0
        for (k, v) in corrected_dict.items():
            if v != v or math.isinf(v):
                corrected_dict[k] = 0
                anything_changed += 1
        return corrected_dict

    def get_object_by_id(self, object_id: str) -> Optional[Dict[str, Any]]:
        for o in self.last_event.metadata["objects"]:
            if o["objectId"] == object_id:
                o["position"] = self.correct_nan_inf(o["position"], "obj id")
                return o
        return None

    def get_current_arm_state(self):
        h_min = ARM_MIN_HEIGHT
        h_max = ARM_MAX_HEIGHT
        agent_base_location = 0.9009995460510254
        event = self.controller.last_event
        offset = event.metadata["agent"]["position"]["y"] - agent_base_location
        h_max += offset
        h_min += offset
        joints = event.metadata["arm"]["joints"]
        #LATER_TODO debug handspherecenter
        arm = joints[-1]
        assert arm["name"] == "robot_arm_4_jnt"
        xyz_dict = copy.deepcopy(arm["rootRelativePosition"])
        # xyz_dict = copy.deepcopy(self.controller.last_event.metadata['arm']['handSphereCenter'])
        height_arm = joints[0]["position"]["y"]
        xyz_dict["h"] = (height_arm - h_min) / (h_max - h_min)
        xyz_dict = self.correct_nan_inf(xyz_dict, "realtive hand")
        return xyz_dict

    def get_absolute_hand_state(self):
        event = self.controller.last_event
        joints = event.metadata["arm"]["joints"]
        arm = copy.deepcopy(joints[-1])
        assert arm["name"] == "robot_arm_4_jnt"
        xyz_dict = arm["position"]
        #LATER_TODO debug hand sphere center
        # xyz_dict = copy.deepcopy(self.controller.last_event.metadata['arm']['handSphereCenter'])


        xyz_dict = self.correct_nan_inf(xyz_dict, "absolute hand")
        return dict(position=xyz_dict, rotation={"x": 0, "y": 0, "z": 0})

    
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
        elif action_dict['action'] is 'RotateRight':
            new_loc["rotation"] = (new_loc["rotation"] + self.rotate_nominal) % 360
        elif action_dict['action'] is 'MoveAhead':
            new_loc["x"] += self.ahead_nominal * np.sin(new_loc["rotation"] * np.pi / 180)
            new_loc["z"] += self.ahead_nominal * np.cos(new_loc["rotation"] * np.pi / 180)
        elif action_dict['action'] is 'TeleportFull':
            new_loc["x"] = action_dict['x']
            new_loc["y"] = action_dict['y']
            new_loc["z"] = action_dict['z']
            new_loc["rotation"] = action_dict['rotation']['y']
            new_loc["horizon"] = action_dict['horizon']
        
        self.nominal_agent_location = new_loc

    def get_pickupable_objects(self):

        event = self.controller.last_event
        object_list = event.metadata["arm"]["pickupableObjects"]

        return object_list

    def get_current_object_locations(self):
        obj_loc_dict = {}
        metadata = self.controller.last_event.metadata["objects"]
        for o in metadata:
            obj_loc_dict[o["objectId"]] = dict(
                position=o["position"], rotation=o["rotation"]
            )
        return copy.deepcopy(obj_loc_dict)

    def close_enough(self, current_obj_pose, init_obj_pose, threshold):
        position_close = [
            abs(current_obj_pose["position"][k] - init_obj_pose["position"][k])
            <= threshold
            for k in ["x", "y", "z"]
        ]
        position_is_close = sum(position_close) == 3
        # rotation_close = [
        #     abs(current_obj_pose["rotation"][k] - init_obj_pose["rotation"][k])
        #     <= threshold
        #     for k in ["x", "y", "z"]
        # ]
        # rotation_is_close = sum(rotation_close) == 3
        return position_is_close # and rotation_is_close

    def get_objects_moved(self, initial_object_locations):
        current_object_locations = self.get_current_object_locations()
        moved_objects = []
        for object_id in current_object_locations.keys():
            if not self.close_enough(
                    current_object_locations[object_id],
                    initial_object_locations[object_id],
                    threshold=OBJECTS_MOVE_THR,
            ):
                moved_objects.append(object_id)

        return moved_objects
    def update_memory(self):
        rgb = self.controller.last_event.frame.copy()
        depth = self.controller.last_event.depth_frame.copy()
        event = copy.deepcopy(self.controller.last_event.metadata)
        # depth = depth[...,np.newaxis]
        current_frame = {
            'rgb': rgb,
            'depth': depth,
            'event':event,
        }
        # if len(self.memory_frames) == 0:
        #     self.memory_frames = [current_frame for _ in range(self.MEMORY_SIZE)]
        # else:
        #     self.memory_frames = self.memory_frames[1:] + [current_frame]
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

        elif action in [MOVE_AHEAD, ROTATE_RIGHT, ROTATE_LEFT]:

            copy_aditions = copy.deepcopy(ADITIONAL_ARM_ARGS)

            # RH: order matters, nominal action happens last
            action_dict = {**action_dict, **copy_aditions}
            if action in [MOVE_AHEAD]:
                noise = self.noise_model.get_ahead_drift(self.ahead_nominal)

                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2]
                sr = self.controller.step(action_dict)

                action_dict = dict()
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = self.ahead_nominal + noise[0]
                action_dict["right"] = noise[1]

            elif action in [ROTATE_RIGHT]:
                noise = self.noise_model.get_rotate_drift()
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0]
                action_dict["right"] = noise[1]
                sr = self.controller.step(action_dict)

                action_dict = dict()
                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2] + self.rotate_nominal

            elif action in [ROTATE_LEFT]:
                noise = self.noise_model.get_rotate_drift()
                action_dict["action"] = "MoveAgent"
                action_dict["ahead"] = noise[0]
                action_dict["right"] = noise[1]
                sr = self.controller.step(action_dict)

                action_dict = dict()
                action_dict["action"] = "RotateAgent"
                action_dict["degrees"] = noise[2] - self.rotate_nominal

        elif "MoveArm" in action:
            copy_aditions = copy.deepcopy(ADITIONAL_ARM_ARGS)
            action_dict = {**action_dict, **copy_aditions}
            base_position = self.get_current_arm_state()
            if "MoveArmHeight" in action:
                action_dict["action"] = "MoveArmBase"

                if action == MOVE_ARM_HEIGHT_P:
                    base_position["h"] += MOVE_ARM_HEIGHT_CONSTANT
                if action == MOVE_ARM_HEIGHT_M:
                    base_position[
                        "h"
                    ] -= MOVE_ARM_HEIGHT_CONSTANT  # height is pretty big!
                action_dict["y"] = base_position["h"]
            else:
                action_dict["action"] = "MoveArm"
                if action == MOVE_ARM_X_P:
                    base_position["x"] += MOVE_ARM_CONSTANT
                elif action == MOVE_ARM_X_M:
                    base_position["x"] -= MOVE_ARM_CONSTANT
                elif action == MOVE_ARM_Y_P:
                    base_position["y"] += MOVE_ARM_CONSTANT
                elif action == MOVE_ARM_Y_M:
                    base_position["y"] -= MOVE_ARM_CONSTANT
                elif action == MOVE_ARM_Z_P:
                    base_position["z"] += MOVE_ARM_CONSTANT
                elif action == MOVE_ARM_Z_M:
                    base_position["z"] -= MOVE_ARM_CONSTANT
                action_dict["position"] = {
                    k: v for (k, v) in base_position.items() if k in ["x", "y", "z"]
                }

        sr = self.controller.step(action_dict)
        self.list_of_actions_so_far.append(action_dict)
        
        # RH: Nominal location only updates for successful actions. Note that that drift 
        # action might succeed even if the "main" action fails
        if sr.metadata["lastActionSuccess"]:
            self.update_nominal_location(original_action_dict)

        if action in SET_OF_ALL_AGENT_ACTIONS:
            self.update_memory()

        if self._verbose:
            print(self.controller.last_event)

        if self.restrict_to_initially_reachable_points:
            self._snap_agent_to_initially_reachable()

        if skip_render:
            assert last_frame is not None
            self.last_event.frame = last_frame

        return sr

