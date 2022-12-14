"""Task Definions for the task of ArmPointNav"""
import copy
import math
from typing import Dict, Tuple, List, Any, Optional

import gym
import numpy as np
import torch
from allenact.base_abstractions.misc import RLStepResult
from allenact.base_abstractions.sensor import Sensor
from allenact.base_abstractions.task import Task

from ithor_arm.ithor_arm_constants import (
    MOVE_ARM_CONSTANT,
    MOVE_ARM_HEIGHT_P,
    MOVE_ARM_HEIGHT_M,
    MOVE_ARM_X_P,
    MOVE_ARM_X_M,
    MOVE_ARM_Y_P,
    MOVE_ARM_Y_M,
    MOVE_ARM_Z_P,
    MOVE_ARM_Z_M,
    MOVE_AHEAD,
    ROTATE_RIGHT,
    ROTATE_LEFT,
    PICKUP,
    DONE, ARM_LENGTH, ARM_ACTIONS_ORDERED, ARM_SHORTENED_ACTIONS_ORDERED,
)
from ithor_arm.ithor_arm_environment import ManipulaTHOREnvironment
from ithor_arm.ithor_arm_viz import LoggerVisualizer
from manipulathor_utils.debugger_util import ForkedPdb
from scripts.dataset_generation.find_categories_to_use import get_room_type_from_id
from scripts.hacky_objects_that_move import CONSTANTLY_MOVING_OBJECTS
from scripts.jupyter_helper import get_reachable_positions


def position_distance(s1, s2):
    position1 = s1["position"]
    position2 = s2["position"]
    return (
        (position1["x"] - position2["x"]) ** 2
        + (position1["y"] - position2["y"]) ** 2
        + (position1["z"] - position2["z"]) ** 2
    ) ** 0.5


class AbstractBringObjectTask(Task[ManipulaTHOREnvironment]):

    _actions = (
        # MOVE_ARM_HEIGHT_P,
        # MOVE_ARM_HEIGHT_M,
        # MOVE_ARM_X_P,
        # MOVE_ARM_X_M,
        # MOVE_ARM_Y_P,
        # MOVE_ARM_Y_M,
        # MOVE_ARM_Z_P,
        # MOVE_ARM_Z_M,
        # MOVE_AHEAD,
        # ROTATE_RIGHT,
        # ROTATE_LEFT,
    )

    def __init__(
        self,
        env: ManipulaTHOREnvironment,
        sensors: List[Sensor],
        task_info: Dict[str, Any],
        max_steps: int,
        visualizers: List[LoggerVisualizer] = [],
        **kwargs
    ) -> None:
        """Initializer.

        See class documentation for parameter definitions.
        """
        super().__init__(
            env=env, sensors=sensors, task_info=task_info, max_steps=max_steps, **kwargs
        )
        self._took_end_action: bool = False
        self._success: Optional[bool] = False
        self._subsampled_locations_from_which_obj_visible: Optional[
            List[Tuple[float, float, int, int]]
        ] = None
        self.visualizers = visualizers
        self.start_visualize()
        self.action_sequence_and_success = []
        self._took_end_action: bool = False
        self._success: Optional[bool] = False
        self._subsampled_locations_from_which_obj_visible: Optional[
            List[Tuple[float, float, int, int]]
        ] = None

        # in allenact initialization is with 0.2
        self.last_obj_to_goal_distance = None
        self.last_arm_to_obj_distance = None
        self.object_picked_up = False
        self.got_reward_for_pickup = False
        self.reward_configs = kwargs["reward_configs"]
        self.initial_object_metadata = self.env.get_current_object_locations()
        self._last_action_str = None

    @property
    def action_space(self):
        return gym.spaces.Discrete(len(self._actions))

    def reached_terminal_state(self) -> bool:
        return self._took_end_action

    @classmethod
    def class_action_names(cls, **kwargs) -> Tuple[str, ...]:
        return cls._actions

    def close(self) -> None:
        self.env.stop()

    def objects_close_enough(self, s1, s2):
        position1 = s1["position"]
        position2 = s2["position"]
        eps = 0.2 # is this a good value?
        return (
            abs(position1["x"] - position2["x"]) < eps
            and abs(position1["y"] - position2["y"]) < eps
            and abs(position1["z"] - position2["z"]) < eps
        )

    def start_visualize(self):
        for visualizer in self.visualizers:
            if not visualizer.is_empty():
                print("OH NO VISUALIZER WAS NOT EMPTY")
                visualizer.finish_episode(self.env, self, self.task_info)
                visualizer.finish_episode_metrics(self, self.task_info, None)
            visualizer.log(self.env, "")

    def visualize(self, action_str):

        for vizualizer in self.visualizers:
            vizualizer.log(self.env, action_str)

    def finish_visualizer(self, episode_success):

        for visualizer in self.visualizers:
            visualizer.finish_episode(self.env, self, self.task_info)

    def finish_visualizer_metrics(self, metric_results):

        for visualizer in self.visualizers:
            visualizer.finish_episode_metrics(self, self.task_info, metric_results)

    def render(self, mode: str = "rgb", *args, **kwargs) -> np.ndarray:
        assert mode == "rgb", "only rgb rendering is implemented"
        return self.env.current_frame

    def calc_action_stat_metrics(self) -> Dict[str, Any]:
        action_stat = {
            "metric/action_stat/" + action_str: 0.0 for action_str in self._actions
        }
        action_success_stat = {
            "metric/action_success/" + action_str: 0.0 for action_str in self._actions
        }
        action_success_stat["metric/action_success/total"] = 0.0

        seq_len = len(self.action_sequence_and_success)
        for (action_name, action_success) in self.action_sequence_and_success:
            action_stat["metric/action_stat/" + action_name] += 1.0
            action_success_stat[
                "metric/action_success/{}".format(action_name)
            ] += action_success
            action_success_stat["metric/action_success/total"] += action_success

        action_success_stat["metric/action_success/total"] /= seq_len

        for action_name in self._actions:
            action_success_stat[
                "metric/" + "action_success/{}".format(action_name)
            ] /= (action_stat["metric/action_stat/" + action_name] + 0.000001)
            action_stat["metric/action_stat/" + action_name] /= seq_len

        # succ = [v for v in action_success_stat.values()]
        # sum(succ) / len(succ) TODO why is this on the air
        result = {**action_stat, **action_success_stat}

        return result

    def metrics(self) -> Dict[str, Any]:
        result = super(AbstractBringObjectTask, self).metrics()
        if self.is_done():
            result = {**result, **self.calc_action_stat_metrics()}
            final_obj_distance_from_goal = self.obj_distance_from_goal()
            result[
                "metric/average/final_obj_distance_from_goal"
            ] = final_obj_distance_from_goal
            final_arm_distance_from_obj = self.arm_distance_from_obj()
            result[
                "metric/average/final_arm_distance_from_obj"
            ] = final_arm_distance_from_obj
            final_obj_pickup = 1 if self.object_picked_up else 0
            result["metric/average/final_obj_pickup/total"] = final_obj_pickup

            original_distance = self.get_original_object_distance()
            result["metric/average/original_distance"] = original_distance

            category_name = self.task_info['source_object_id'].split('|')[0]
            result[f'metric/average/final_obj_pickup/{category_name}'] = self.object_picked_up
            if self.object_picked_up:
                destination_name = self.task_info['goal_object_id'].split('|')[0]
                result[f'metric/average/final_success/{destination_name}'] = self._success

            # this ratio can be more than 1?
            if self.object_picked_up:
                ratio_distance_left = final_obj_distance_from_goal / (original_distance + 1e-9)
                result["metric/average/ratio_distance_left"] = ratio_distance_left
                result["metric/average/eplen_pickup"] = self.eplen_pickup
            result["metric/average/success_wo_disturb"] = (
                    0
            )
            if self._success:
                result["metric/average/eplen_success"] = result["ep_length"]
                # put back this is not the reason for being slow
                objects_moved = self.env.get_objects_moved(self.initial_object_metadata)
                # Unnecessary, this is definitely happening objects_moved.remove(self.task_info['object_id'])
                source_obj = self.task_info['source_object_id']
                destination_obj = self.task_info['goal_object_id']
                if source_obj in objects_moved:
                    objects_moved.remove(source_obj)
                if destination_obj in objects_moved:
                    objects_moved.remove(destination_obj)
                if self.env.scene_name in CONSTANTLY_MOVING_OBJECTS:
                    should_be_removed = CONSTANTLY_MOVING_OBJECTS[self.env.scene_name]
                    for k in should_be_removed:
                        if k in objects_moved:
                            objects_moved.remove(k)

                result["metric/average/number_of_unwanted_moved_objects"] = (
                    len(objects_moved)
                )
                result["metric/average/success_wo_disturb"] = (
                    len(objects_moved) == 0
                )  # multiply this by the successrate

            result["success"] = self._success

            self.finish_visualizer_metrics(result)
            self.finish_visualizer(self._success)
            self.action_sequence_and_success = []
            # result['task_info'] = copy.deepcopy(result['task_info'])
            # for remove_feature in ['source_object_query', 'goal_object_query','source_object_query_feature', 'goal_object_query_feature',]:
            #     result['task_info'][remove_feature] = []

            result['task_info'] = copy.deepcopy(result['task_info'])
            for feature_to_remove in ['source_object_query', 'goal_object_query','source_object_query_feature', 'goal_object_query_feature',]:
                result['task_info'][feature_to_remove] = []
        return result

    def _step(self, action: int) -> RLStepResult:
        raise Exception("Not implemented")

    def arm_distance_from_obj(self):
        source_object_id = self.task_info["source_object_id"]
        object_info = self.env.get_object_by_id(source_object_id)
        hand_state = self.env.get_absolute_hand_state()
        return position_distance(object_info, hand_state)

    def obj_distance_from_goal(self):
        source_object_id = self.task_info["source_object_id"]
        source_object_info = self.env.get_object_by_id(source_object_id)
        goal_object_id = self.task_info["goal_object_id"]
        goal_object_info = self.env.get_object_by_id(goal_object_id)
        return position_distance(source_object_info, goal_object_info)

    def get_original_object_distance(self):
        source_object_id = self.task_info["source_object_id"]
        s_init = dict(position=self.task_info["init_location"]["object_location"])
        current_location = self.env.get_object_by_id(source_object_id)

        original_object_distance = position_distance(s_init, current_location)
        return original_object_distance

    def judge(self) -> float:
        """Compute the reward after having taken a step."""
        raise Exception("Not implemented")



class BringObjectTask(AbstractBringObjectTask):
    _actions = (
        MOVE_ARM_HEIGHT_P,
        MOVE_ARM_HEIGHT_M,
        MOVE_ARM_X_P,
        MOVE_ARM_X_M,
        MOVE_ARM_Y_P,
        MOVE_ARM_Y_M,
        MOVE_ARM_Z_P,
        MOVE_ARM_Z_M,
        MOVE_AHEAD,
        ROTATE_RIGHT,
        ROTATE_LEFT,
        # PICKUP,
        # DONE,
    )
    def _step(self, action: int) -> RLStepResult:

        action_str = self.class_action_names()[action]

        self.manual = False
        if self.manual:
            action_str = 'something'
            # actions = ()
            # actions_short  = ('u', 'j', 's', 'a', '3', '4', 'w', 'z', 'm', 'r', 'l')
            # ARM_ACTIONS_ORDERED = [MOVE_ARM_HEIGHT_P,MOVE_ARM_HEIGHT_M,MOVE_ARM_X_P,MOVE_ARM_X_M,MOVE_ARM_Y_P,MOVE_ARM_Y_M,MOVE_ARM_Z_P,MOVE_ARM_Z_M,MOVE_AHEAD,ROTATE_RIGHT,ROTATE_LEFT]
            # ARM_SHORTENED_ACTIONS_ORDERED = ['u','j','s','a','3','4','w','z','m','r','l']
            action = 'm'
            self.env.controller.step('Pass')
            print(self.task_info['source_object_id'], self.task_info['goal_object_id'], 'pickup', self.object_picked_up)
            ForkedPdb().set_trace()
            action_str = ARM_ACTIONS_ORDERED[ARM_SHORTENED_ACTIONS_ORDERED.index(action)]


        self._last_action_str = action_str
        action_dict = {"action": action_str}
        object_id = self.task_info["source_object_id"]
        if action_str == PICKUP:
            action_dict = {**action_dict, "object_id": object_id}
        self.env.step(action_dict)
        self.last_action_success = self.env.last_action_success

        last_action_name = self._last_action_str
        last_action_success = float(self.last_action_success)
        self.action_sequence_and_success.append((last_action_name, last_action_success))
        self.visualize(last_action_name)

        if not self.object_picked_up:
            if object_id in self.env.controller.last_event.metadata['arm']['pickupableObjects']:
                event = self.env.step(dict(action="PickupObject"))
                #  we are doing an additional pass here, label is not right and if we fail we will do it twice
                object_inventory = self.env.controller.last_event.metadata["arm"][
                    "heldObjects"
                ]
                if (
                        len(object_inventory) > 0
                        and object_id not in object_inventory
                ):
                    event = self.env.step(dict(action="ReleaseObject"))

            if self.env.is_object_at_low_level_hand(object_id):
                self.object_picked_up = True
                self.eplen_pickup = (
                        self._num_steps_taken + 1
                )  # plus one because this step has not been counted yet

        if self.object_picked_up:


            # self._took_end_action = True
            # self.last_action_success = True
            # self._success = True

            source_state = self.env.get_object_by_id(object_id)
            goal_state = self.env.get_object_by_id(self.task_info['goal_object_id'])
            goal_achieved = self.object_picked_up and self.objects_close_enough(
                source_state, goal_state
            )
            if goal_achieved:
                self._took_end_action = True
                self.last_action_success = goal_achieved
                self._success = goal_achieved

        step_result = RLStepResult(
            observation=self.get_observations(),
            reward=self.judge(),
            done=self.is_done(),
            info={"last_action_success": self.last_action_success},
        )
        return step_result


    def judge(self) -> float:
        """Compute the reward after having taken a step."""
        reward = self.reward_configs["step_penalty"]

        if not self.last_action_success or (
                self._last_action_str == PICKUP and not self.object_picked_up
        ):
            reward += self.reward_configs["failed_action_penalty"]

        if self._took_end_action:
            reward += (
                self.reward_configs["goal_success_reward"]
                if self._success
                else self.reward_configs["failed_stop_reward"]
            )

        # increase reward if object pickup and only do it once
        if not self.got_reward_for_pickup and self.object_picked_up:
            reward += self.reward_configs["pickup_success_reward"]
            self.got_reward_for_pickup = True


        current_obj_to_arm_distance = self.arm_distance_from_obj()
        if self.last_arm_to_obj_distance is None:
            delta_arm_to_obj_distance_reward = 0
        else:
            delta_arm_to_obj_distance_reward = (
                    self.last_arm_to_obj_distance - current_obj_to_arm_distance
            )
        self.last_arm_to_obj_distance = current_obj_to_arm_distance
        reward += delta_arm_to_obj_distance_reward

        current_obj_to_goal_distance = self.obj_distance_from_goal()
        if self.last_obj_to_goal_distance is None:
            delta_obj_to_goal_distance_reward = 0
        else:
            delta_obj_to_goal_distance_reward = (
                self.last_obj_to_goal_distance - current_obj_to_goal_distance
            )
        self.last_obj_to_goal_distance = current_obj_to_goal_distance
        reward += delta_obj_to_goal_distance_reward



        # add collision cost, maybe distance to goal objective,...

        return float(reward)

class WPickUpBringObjectTask(BringObjectTask):
    _actions = (
        MOVE_ARM_HEIGHT_P,
        MOVE_ARM_HEIGHT_M,
        MOVE_ARM_X_P,
        MOVE_ARM_X_M,
        MOVE_ARM_Y_P,
        MOVE_ARM_Y_M,
        MOVE_ARM_Z_P,
        MOVE_ARM_Z_M,
        MOVE_AHEAD,
        ROTATE_RIGHT,
        ROTATE_LEFT,
        PICKUP,
        # DONE,
    )
    def _step(self, action: int) -> RLStepResult:

        action_str = self.class_action_names()[action]

        self._last_action_str = action_str
        action_dict = {"action": action_str}
        object_id = self.task_info["source_object_id"]
        if action_str == PICKUP:
            action_dict = {**action_dict, "object_id": object_id}
        self.env.step(action_dict)
        self.last_action_success = self.env.last_action_success
        if action_str == PICKUP:
            self.last_action_success = self.env.is_object_at_low_level_hand(object_id)
        last_action_name = self._last_action_str
        last_action_success = float(self.last_action_success)
        self.action_sequence_and_success.append((last_action_name, last_action_success))
        self.visualize(last_action_name)

        if not self.object_picked_up: #if not picked up yet
            if self.env.is_object_at_low_level_hand(object_id):
                self.object_picked_up = True
                self.eplen_pickup = (
                        self._num_steps_taken + 1
                )  # plus one because this step has not been counted yet


        # TODO put back after you put back done
        if False and action_str == DONE:
            self._took_end_action = True
            source_state = self.env.get_object_by_id(object_id)
            goal_state = self.env.get_object_by_id(self.task_info['goal_object_id'])
            goal_achieved = self.object_picked_up and self.objects_close_enough(
                source_state, goal_state
            )

            self.last_action_success = goal_achieved
            self._success = goal_achieved


        elif self.object_picked_up:

            source_state = self.env.get_object_by_id(object_id)
            goal_state = self.env.get_object_by_id(self.task_info['goal_object_id'])
            goal_achieved = self.object_picked_up and self.objects_close_enough(
                source_state, goal_state
            )
            if goal_achieved:
                self._took_end_action = True
                self.last_action_success = goal_achieved
                self._success = goal_achieved

        step_result = RLStepResult(
            observation=self.get_observations(),
            reward=self.judge(),
            done=self.is_done(),
            info={"last_action_success": self.last_action_success},
        )
        return step_result


    def judge(self) -> float:
        """Compute the reward after having taken a step."""
        reward = self.reward_configs["step_penalty"]

        if not self.last_action_success or (
                self._last_action_str == PICKUP and not self.object_picked_up
        ):
            reward += self.reward_configs["failed_action_penalty"]

        if self._took_end_action:
            reward += (
                self.reward_configs["goal_success_reward"]
                if self._success
                else self.reward_configs["failed_stop_reward"]
            )

        # increase reward if object pickup and only do it once
        if not self.got_reward_for_pickup and self.object_picked_up:
            reward += self.reward_configs["pickup_success_reward"]
            self.got_reward_for_pickup = True
        #

        current_obj_to_arm_distance = self.arm_distance_from_obj()
        if self.last_arm_to_obj_distance is None:
            delta_arm_to_obj_distance_reward = 0
        else:
            delta_arm_to_obj_distance_reward = (
                    self.last_arm_to_obj_distance - current_obj_to_arm_distance
            )
        self.last_arm_to_obj_distance = current_obj_to_arm_distance
        reward += delta_arm_to_obj_distance_reward

        current_obj_to_goal_distance = self.obj_distance_from_goal()
        if self.last_obj_to_goal_distance is None:
            delta_obj_to_goal_distance_reward = 0
        else:
            delta_obj_to_goal_distance_reward = (
                    self.last_obj_to_goal_distance - current_obj_to_goal_distance
            )
        self.last_obj_to_goal_distance = current_obj_to_goal_distance
        reward += delta_obj_to_goal_distance_reward

        # add collision cost, maybe distance to goal objective,...

        return float(reward)

class NoPickUPExploreBringObjectTask(BringObjectTask):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        all_locations = [[k['x'], k['y'], k['z']] for k in get_reachable_positions(self.env.controller)]
        self.all_reachable_positions = torch.Tensor(all_locations)
        self.has_visited = torch.zeros((len(self.all_reachable_positions), 1))
    def judge(self) -> float:
        """Compute the reward after having taken a step."""
        reward = self.reward_configs["step_penalty"]

        current_agent_location = self.env.get_agent_location()
        current_agent_location = torch.Tensor([current_agent_location['x'], current_agent_location['y'], current_agent_location['z']])
        all_distances = self.all_reachable_positions - current_agent_location
        all_distances = (all_distances ** 2).sum(dim=-1)
        location_index = torch.argmin(all_distances)
        if self.has_visited[location_index] == 0:
            reward += self.reward_configs["exploration_reward"]
        self.has_visited[location_index] = 1


        if not self.last_action_success or (
                self._last_action_str == PICKUP and not self.object_picked_up
        ):
            reward += self.reward_configs["failed_action_penalty"]

        if self._took_end_action:
            reward += (
                self.reward_configs["goal_success_reward"]
                if self._success
                else self.reward_configs["failed_stop_reward"]
            )

        # increase reward if object pickup and only do it once
        if not self.got_reward_for_pickup and self.object_picked_up:
            reward += self.reward_configs["pickup_success_reward"]
            self.got_reward_for_pickup = True
        #

        current_obj_to_arm_distance = self.arm_distance_from_obj()
        if self.last_arm_to_obj_distance is None:
            delta_arm_to_obj_distance_reward = 0
        else:
            delta_arm_to_obj_distance_reward = (
                    self.last_arm_to_obj_distance - current_obj_to_arm_distance
            )
        self.last_arm_to_obj_distance = current_obj_to_arm_distance
        reward += delta_arm_to_obj_distance_reward

        current_obj_to_goal_distance = self.obj_distance_from_goal()
        if self.last_obj_to_goal_distance is None:
            delta_obj_to_goal_distance_reward = 0
        else:
            delta_obj_to_goal_distance_reward = (
                    self.last_obj_to_goal_distance - current_obj_to_goal_distance
            )
        self.last_obj_to_goal_distance = current_obj_to_goal_distance
        reward += delta_obj_to_goal_distance_reward

        # add collision cost, maybe distance to goal objective,...

        return float(reward)

class WPickUPExploreBringObjectTask(WPickUpBringObjectTask):

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        all_locations = [[k['x'], k['y'], k['z']] for k in get_reachable_positions(self.env.controller)]
        self.all_reachable_positions = torch.Tensor(all_locations)
        self.has_visited = torch.zeros((len(self.all_reachable_positions), 1))
    def judge(self) -> float:
        """Compute the reward after having taken a step."""
        reward = self.reward_configs["step_penalty"]

        current_agent_location = self.env.get_agent_location()
        current_agent_location = torch.Tensor([current_agent_location['x'], current_agent_location['y'], current_agent_location['z']])
        all_distances = self.all_reachable_positions - current_agent_location
        all_distances = (all_distances ** 2).sum(dim=-1)
        location_index = torch.argmin(all_distances)
        if self.has_visited[location_index] == 0:
            reward += self.reward_configs["exploration_reward"]
        self.has_visited[location_index] = 1


        if not self.last_action_success or (
                self._last_action_str == PICKUP and not self.object_picked_up
        ):
            reward += self.reward_configs["failed_action_penalty"]

        if self._took_end_action:
            reward += (
                self.reward_configs["goal_success_reward"]
                if self._success
                else self.reward_configs["failed_stop_reward"]
            )

        # increase reward if object pickup and only do it once
        if not self.got_reward_for_pickup and self.object_picked_up:
            reward += self.reward_configs["pickup_success_reward"]
            self.got_reward_for_pickup = True
        #

        current_obj_to_arm_distance = self.arm_distance_from_obj()
        if self.last_arm_to_obj_distance is None:
            delta_arm_to_obj_distance_reward = 0
        else:
            delta_arm_to_obj_distance_reward = (
                    self.last_arm_to_obj_distance - current_obj_to_arm_distance
            )
        self.last_arm_to_obj_distance = current_obj_to_arm_distance
        reward += delta_arm_to_obj_distance_reward

        current_obj_to_goal_distance = self.obj_distance_from_goal()
        if self.last_obj_to_goal_distance is None:
            delta_obj_to_goal_distance_reward = 0
        else:
            delta_obj_to_goal_distance_reward = (
                    self.last_obj_to_goal_distance - current_obj_to_goal_distance
            )
        self.last_obj_to_goal_distance = current_obj_to_goal_distance
        reward += delta_obj_to_goal_distance_reward

        # add collision cost, maybe distance to goal objective,...

        return float(reward)

class ExploreWiseRewardTask(BringObjectTask):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        all_locations = [[k['x'], k['y'], k['z']] for k in (self.env.get_reachable_positions())]
        self.all_reachable_positions = torch.Tensor(all_locations)
        self.has_visited = torch.zeros((len(self.all_reachable_positions), 1))
        self.source_observed_reward = False
        self.goal_observed_reward = False
    def metrics(self) -> Dict[str, Any]:
        result = super(ExploreWiseRewardTask, self).metrics()
        if self.is_done():
            result['percent_room_visited'] = self.has_visited.mean().item()
            room_type = get_room_type_from_id(self.task_info['init_location']['scene_name'])
            metric_by_room_type = {}
            for k, v in result.items():
                if k in ['ep_length', 'reward', 'success', 'metric/average/success_wo_disturb', 'metric/average/final_obj_pickup/total']:
                    metric_by_room_type[f'by_room/{room_type}/{k}'] = v

            result = {**result, **metric_by_room_type}
        return result

    def judge(self) -> float:
        """Compute the reward after having taken a step."""
        reward = self.reward_configs["step_penalty"]


        current_agent_location = self.env.get_agent_location()
        current_agent_location = torch.Tensor([current_agent_location['x'], current_agent_location['y'], current_agent_location['z']])
        all_distances = self.all_reachable_positions - current_agent_location
        all_distances = (all_distances ** 2).sum(dim=-1)
        location_index = torch.argmin(all_distances)
        if self.has_visited[location_index] == 0:
            visited_new_place = True
        else:
            visited_new_place = False
        self.has_visited[location_index] = 1

        if visited_new_place and not self.source_observed_reward:
            reward += self.reward_configs["exploration_reward"]
        elif visited_new_place and self.object_picked_up and not self.goal_observed_reward:
            reward += self.reward_configs["exploration_reward"]


        source_is_visible = self.env.last_event.get_object(self.task_info['source_object_id'])['visible']
        goal_is_visible = self.env.last_event.get_object(self.task_info['goal_object_id'])['visible']

        if not self.source_observed_reward and source_is_visible:
            self.source_observed_reward = True
            reward += self.reward_configs['object_found']

        if not self.goal_observed_reward and goal_is_visible:
            self.goal_observed_reward = True
            reward += self.reward_configs['object_found']

        if not self.last_action_success or (
                self._last_action_str == PICKUP and not self.object_picked_up
        ):
            reward += self.reward_configs["failed_action_penalty"]

        if self._took_end_action:
            reward += (
                self.reward_configs["goal_success_reward"]
                if self._success
                else self.reward_configs["failed_stop_reward"]
            )

        # increase reward if object pickup and only do it once
        if not self.got_reward_for_pickup and self.object_picked_up:
            reward += self.reward_configs["pickup_success_reward"]
            self.got_reward_for_pickup = True
        #



        current_obj_to_arm_distance = self.arm_distance_from_obj()
        if self.last_arm_to_obj_distance is None or self.last_arm_to_obj_distance > ARM_LENGTH * 2: # is this good?
            delta_arm_to_obj_distance_reward = 0
        else:
            delta_arm_to_obj_distance_reward = (
                    self.last_arm_to_obj_distance - current_obj_to_arm_distance
            )
        self.last_arm_to_obj_distance = current_obj_to_arm_distance
        reward += delta_arm_to_obj_distance_reward * self.reward_configs["arm_dist_multiplier"]

        current_obj_to_goal_distance = self.obj_distance_from_goal()
        if self.last_obj_to_goal_distance is None or self.last_obj_to_goal_distance > ARM_LENGTH * 2:
            delta_obj_to_goal_distance_reward = 0
        else:
            delta_obj_to_goal_distance_reward = (
                    self.last_obj_to_goal_distance - current_obj_to_goal_distance
            )
        self.last_obj_to_goal_distance = current_obj_to_goal_distance * self.reward_configs["arm_dist_multiplier"]
        reward += delta_obj_to_goal_distance_reward

        # add collision cost, maybe distance to goal objective,...

        #TODO remove as soon as bug is resolved
        if math.isinf(reward) or math.isnan(reward):
            print('reward is none', reward)
            print('delta_obj_to_goal_distance_reward', delta_obj_to_goal_distance_reward)
            print('delta_arm_to_obj_distance_reward', delta_arm_to_obj_distance_reward)
            print('room', self.task_info['scene_name'])

        return float(reward)

class ExploreWiseRewardTaskWPU(ExploreWiseRewardTask):
    _actions = (
        MOVE_ARM_HEIGHT_P,
        MOVE_ARM_HEIGHT_M,
        MOVE_ARM_X_P,
        MOVE_ARM_X_M,
        MOVE_ARM_Y_P,
        MOVE_ARM_Y_M,
        MOVE_ARM_Z_P,
        MOVE_ARM_Z_M,
        MOVE_AHEAD,
        ROTATE_RIGHT,
        ROTATE_LEFT,
        PICKUP,
    )
    def _step(self, action: int) -> RLStepResult:
        action_str = self.class_action_names()[action]

        self._last_action_str = action_str
        action_dict = {"action": action_str}
        object_id = self.task_info["source_object_id"]
        if action_str == PICKUP:
            action_dict = {**action_dict, "object_id": object_id}
        self.env.step(action_dict)
        self.last_action_success = self.env.last_action_success

        last_action_name = self._last_action_str
        last_action_success = float(self.last_action_success)
        self.action_sequence_and_success.append((last_action_name, last_action_success))
        self.visualize(last_action_name)

        if not self.object_picked_up:
            # if object_id in self.env.controller.last_event.metadata['arm']['pickupableObjects']:
            #     event = self.env.step(dict(action="PickupObject"))
            #     #  we are doing an additional pass here, label is not right and if we fail we will do it twice
            #     object_inventory = self.env.controller.last_event.metadata["arm"][
            #         "heldObjects"
            #     ]
            #     if (
            #             len(object_inventory) > 0
            #             and object_id not in object_inventory
            #     ):
            #         event = self.env.step(dict(action="ReleaseObject"))
            #
            if self.env.is_object_at_low_level_hand(object_id):
                self.object_picked_up = True
                self.eplen_pickup = (
                        self._num_steps_taken + 1
                )  # plus one because this step has not been counted yet

        if self.object_picked_up:


            # self._took_end_action = True
            # self.last_action_success = True
            # self._success = True

            source_state = self.env.get_object_by_id(object_id)
            goal_state = self.env.get_object_by_id(self.task_info['goal_object_id'])
            goal_achieved = self.object_picked_up and self.objects_close_enough(
                source_state, goal_state
            )
            if goal_achieved:
                self._took_end_action = True
                self.last_action_success = goal_achieved
                self._success = goal_achieved

        step_result = RLStepResult(
            observation=self.get_observations(),
            reward=self.judge(),
            done=self.is_done(),
            info={"last_action_success": self.last_action_success},
        )
        return step_result

# class TestPointNavExploreWiseRewardTask(ExploreWiseRewardTask):
#     def _step(self, action):
#         step_result = super(TestPointNavExploreWiseRewardTask, self)._step(action)
#         pred_pointnav_source= step_result.observation['point_nav_emul_source']
#         real_pointnav_source= step_result.observation['point_nav_real_source']
#         pred_pointnav_destination= step_result.observation['point_nav_emul_destination']
#         real_pointnav_destination= step_result.observation['point_nav_real_destination']
#         if self.num_steps_taken() == 0:
#             self.source_diff = []
#             self.destination_diff = []
#         if not torch.all(pred_pointnav_source == 4):
#             TODO is the abs our problem???
#             diff = (pred_pointnav_source.abs() - real_pointnav_source).norm()
#             self.source_diff.append(diff)
#         if not torch.all(pred_pointnav_destination == 4):
#             diff = (pred_pointnav_destination.abs() - real_pointnav_destination).norm()
#             self.destination_diff.append(diff)
#
#         return step_result
#
#     def metrics(self) -> Dict[str, Any]:
#         result = super(TestPointNavExploreWiseRewardTask, self).metrics()
#         if self.is_done():
#             result['diff_pointnav_source'] = sum(self.source_diff) / (len(self.source_diff) + 1e-9)
#             result['diff_pointnav_destination'] = sum(self.destination_diff) / (len(self.destination_diff) + 1e-9)
#         return result