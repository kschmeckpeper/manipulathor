import os
from datetime import datetime

import cv2
from torch.distributions.utils import lazy_property

from ithor_arm.ithor_arm_viz import LoggerVisualizer
from utils.hacky_viz_utils import save_image_list_to_gif, put_action_on_image, put_additional_text_on_image, \
    depth_to_rgb
import numpy as np

from manipulathor_utils.debugger_util import ForkedPdb
from scripts.stretch_jupyter_helper import transport_wrapper, reset_environment_and_additional_commands
from utils.stretch_utils.stretch_sim2real_utils import kinect_reshape, intel_reshape


class StretchBringObjImageVisualizer(LoggerVisualizer):
    def finish_episode(self, environment, episode_info, task_info):
        now = datetime.now()
        time_to_write = now.strftime("%m_%d_%Y_%H_%M_%S_%f")
        time_to_write += "log_ind_{}".format(self.logger_index)
        self.logger_index += 1
        print("Loggigng", time_to_write, "len", len(self.log_queue))

        source_object_id = task_info["source_object_id"]
        goal_object_id = task_info["goal_object_id"]
        pickup_success = episode_info.object_picked_up
        episode_success = episode_info._success

        # Put back if you want the images
        # for i, img in enumerate(self.log_queue):
        #     image_dir = os.path.join(self.log_dir, time_to_write + '_seq{}.png'.format(str(i)))
        #     cv2.imwrite(image_dir, img[:,:,[2,1,0]])

        episode_success_offset = "succ" if episode_success else "fail"
        pickup_success_offset = "succ" if pickup_success else "fail"


        source_obj_type = source_object_id.split("|")[0]
        goal_obj_type = goal_object_id.split("|")[0]

        if source_obj_type == 'small':
            source_obj_type = task_info['source_object_type']
            goal_obj_type = task_info['goal_object_type']

        room_name = task_info['scene_name']
        gif_name = (
                f"{time_to_write}_room_{room_name}_from_{source_obj_type}_to_{goal_obj_type}_pickup_{pickup_success_offset}_episode_{episode_success_offset}.gif"
        )
        self.log_queue = put_action_on_image(self.log_queue, self.action_queue[1:])
        addition_texts = ['xxx'] + [str(x) for x in episode_info.agent_body_dist_to_obj]
        self.log_queue = put_additional_text_on_image(self.log_queue, addition_texts)
        # concat_all_images = np.expand_dims(np.stack(self.arm_frame_queue, axis=0), axis=1)
        # arm_frames = np.expand_dims(np.stack(self.log_queue, axis=0), axis=1)
        # concat_all_images = np.concatenate([concat_all_images, arm_frames], axis=3)
        concat_all_images = np.expand_dims(np.stack(self.log_queue, axis=0), axis=1)
        save_image_list_to_gif(concat_all_images, gif_name, self.log_dir)
        this_controller = environment.controller
        scene = this_controller.last_event.metadata[
            "sceneName"
        ]
        reset_environment_and_additional_commands(this_controller, scene)

        additional_observation_start = []
        additional_observation_goal = []
        if 'target_object_mask' in episode_info.get_observations():
            additional_observation_start.append('target_object_mask')
        if 'target_location_mask' in episode_info.get_observations():
            additional_observation_goal.append('target_location_mask')

        if 'visualization_source' in task_info and 'visualization_target' in task_info:

            self.log_start_goal(
                environment,
                task_info["visualization_source"],
                tag="start",
                img_adr=os.path.join(self.log_dir, time_to_write),
                additional_observations=additional_observation_start,
                episode_info=episode_info
            )
            self.log_start_goal(
                environment,
                task_info["visualization_target"],
                tag="goal",
                img_adr=os.path.join(self.log_dir, time_to_write),
                additional_observations=additional_observation_goal,
                episode_info=episode_info
            )

        self.log_queue = []
        self.action_queue = []
        self.arm_frame_queue = []

    def log(self, environment, action_str):
        image_intel = environment.intel_frame
        depth_intel = environment.intel_depth
        image_kinect = environment.kinect_frame
        depth_kinect = environment.kinect_depth

        # image_intel = intel_reshape(image_intel)
        # kinect_frame = kinect_reshape(kinect_frame)

        self.action_queue.append(action_str)
        combined_frame = np.concatenate([image_intel, image_kinect, depth_to_rgb(depth_intel), depth_to_rgb(depth_kinect)],axis=1)
        self.log_queue.append(combined_frame)


    @lazy_property
    def arm_frame_queue(self):
        return []


    def log_start_goal(self, env, task_info, tag, img_adr, additional_observations=[], episode_info=None):
        object_location = task_info["object_location"]
        object_id = task_info["object_id"]
        agent_state = task_info["agent_pose"]
        this_controller = env.controller
        scene = this_controller.last_event.metadata[
            "sceneName"
        ]  # maybe we need to reset env actually]
        #We should not reset here
        # for start arm from high up as a cheating, this block is very important. never remov

        event, details = transport_wrapper(this_controller, object_id, object_location)
        if event.metadata["lastActionSuccess"] == False:
            print("ERROR: oh no could not transport in logging", event)

        event = this_controller.step(
            dict(
                action="TeleportFull",
                standing=True,
                x=agent_state["position"]["x"],
                y=agent_state["position"]["y"],
                z=agent_state["position"]["z"],
                rotation=dict(
                    x=agent_state["rotation"]["x"],
                    y=agent_state["rotation"]["y"],
                    z=agent_state["rotation"]["z"],
                ),
                horizon=agent_state["cameraHorizon"],
            )
        )
        if event.metadata["lastActionSuccess"] == False:
            print("ERROR: oh no could not teleport in logging")

        image_tensor = this_controller.last_event.frame
        obj_name = object_id.split("|")[0]

        image_dir = (
                img_adr + f"_init_{tag}_obj_{obj_name}.png"
        )
        cv2.imwrite(image_dir, image_tensor[:, :, [2, 1, 0]])

        # Saving the mask
        if len(additional_observations) > 0:
            observations = episode_info.get_observations()
            for sensor_name in additional_observations:
                assert sensor_name in observations
                mask_frame = (observations[sensor_name])
                mask_dir = (
                        # img_adr + "_obj_" + object_id.split("|")[0] + "_pickup_" + tag + "_{}.png".format(sensor_name)
                        img_adr + f"_sensor_{tag}_{sensor_name}_obj_{obj_name}.png"
                )
                cv2.imwrite(mask_dir, mask_frame.astype(float)*255.)

