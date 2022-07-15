import supersuit, gym
import torch
import numpy as np
from common.wrappers import reward_lambda_v1, zero_sum_reward_filer, SSVecWrapper
from nash_dqn import NashDQN
from common.args_parser import get_args


def rollout(env, model, args):
    """Function to rollout experience as interaction of agents and environments, in
    a typical manner of reinforcement learning. 

    :param env: environment instance
    :type env: object
    :param model: the multi-agent model containing models for all agents
    :type model: MultiAgent
    :param args: arguments
    :type args: ConfigurationDict
    """
    ## Initialization
    print("Arguments: ", args)
    overall_steps = 0

    def choose_action(states, args, model):
        greedy = True if args.test else False
        if args.marl_spec['global_state']:
            actions = model.choose_action(states, Greedy=greedy)
        else:
            actions = model.choose_action(np.expand_dims(states[0], 0), Greedy=greedy) 
        return actions       

    ## Rollout
    for epi in range(args.max_episodes):
        obs = env.reset()
        for step in range(args.max_steps_per_episode):
            overall_steps += 1
            obs_to_store = obs.swapaxes(0, 1) if args.num_envs > 1 else obs  # transform from (envs, agents, dim) to (agents, envs, dim)
            with torch.no_grad():
                action_ = choose_action(
                    obs_to_store, args, model)  # action: (agent, env, action_dim)
            if overall_steps % 100 == 0: # do not need to do this for every step
                model.scheduler_step(overall_steps)
            
            action_to_store = action_
            if args.num_envs > 1:
                action = np.array(action_to_store).swapaxes(0, 1)  # transform from (agents, envs, dim) to (envs, agents, dim)
            else:
                action = action_to_store

            obs_, reward, done, info = env.step(action)  # required action shape: (envs, agents, dim)

            if args.render:
                env.render()

            ## Storage information processing
            if args.num_envs > 1:  # transform from (envs, agents, dim) to (agents, envs, dim)
                obs__to_store = obs_.swapaxes(0, 1)
                reward_to_store = reward.swapaxes(0, 1)
                done_to_store = done.swapaxes(0, 1)
            else:
                obs__to_store = obs_
                reward_to_store = reward
                done_to_store = done

            sample = [  # each item has shape: (agents, envs, dim)
                obs_to_store, action_to_store, reward_to_store,
                obs__to_store, done_to_store
            ]
            
            [states, actions, rewards, next_states, dones] = sample
            if args.num_envs > 1:  # when num_envs > 1. 
                if args.marl_spec['global_state']:  # use concatenated observation from both agents
                    sample = [[states[:, j].reshape(-1), actions[:, j].reshape(-1), rewards[0, j], next_states[:, j].reshape(-1), np.any(d)] for j, d in enumerate(np.array(dones).T)]
                else:  # only use the observation from the first agent (assume the symmetry in the game and the single state contains the full information: speed up learning!)
                    sample = [[states[0, j], actions[:, j].reshape(-1), rewards[0, j], next_states[0, j], np.any(d)] for j, d in enumerate(np.array(dones).T)]
            else:  # when num_envs = 1 
                if args.marl_spec['global_state']: 
                    sample = [[np.array(states).reshape(-1), actions, rewards[0], np.array(next_states).reshape(-1), np.all(dones)]]  # done for both player
                else:
                    sample = [[np.array(states[0]), actions, rewards[0], np.array(next_states[0]), np.all(dones)]]
            model.store(sample)

            obs = obs_
            # logger.log_reward(np.array(reward).reshape(-1))
            loss = None

            # Non-epsodic update of the model
            if not args.algorithm_spec['episodic_update'] and overall_steps > args.train_start_frame \
                and model.buffer.get_len() > args.batch_size:
                if args.update_itr >= 1:
                    avg_loss = []
                    for _ in range(args.update_itr):
                        loss = model.update(
                        )
                        avg_loss.append(loss)
                    loss = np.mean(avg_loss, axis=0)
                elif overall_steps * args.update_itr % 1 == 0:
                    loss = model.update()
                # if loss is not None:
                    # logger.log_loss(loss)

            ## done break: needs to go after everything else， including the update
            if np.any(
                    done
            ):  # if any player in a game is done, the game episode done; may not be correct for some envs
                break
        
        print(epi, reward, loss)
            
        # logger.log_episode_reward(step)

        ## Evaluation during exploiter training
        # if epi % args.log_interval == 0:
        #     if args.exploit:
        #         eval(env, model, logger, epi, args)

            # logger.print_and_save()

        ## Model saving and logging
        # if epi % args.save_interval == 0 \
        #     and logger.model_dir is not None:
        #     model.save_model(logger.model_dir+f'{epi}')


# PettingZoo envs
pettingzoo_envs = {
    'atari': [
        'basketball_pong_v3', 'boxing_v2', 'combat_plane_v1', 'combat_tank_v2',
        'double_dunk_v3', 'entombed_competitive_v3', 'entombed_cooperative_v3',
        'flag_capture_v2', 'foozpong_v3', 'ice_hockey_v2', 'joust_v3',
        'mario_bros_v3', 'maze_craze_v3', 'othello_v3', 'pong_v3',
        'quadrapong_v4', 'space_invaders_v2', 'space_war_v2', 'surround_v2',
        'tennis_v3', 'video_checkers_v4', 'volleyball_pong_v2', 'warlords_v3',
        'wizard_of_wor_v3'
    ],

    'classic': [
        'dou_dizhu_v4', 'go_v5', 'leduc_holdem_v4', 'rps_v2',
        'texas_holdem_no_limit_v6', 'texas_holdem_v4', 'tictactoe_v3', 'uno_v4'
    ]
}

for env_type, envs in pettingzoo_envs.items():
    for env_name in envs:
        try:
            exec("from pettingzoo.{} import {}".format(env_type.lower(), env_name))
            # print(f"Successfully import {env_type} env in PettingZoo: ", env_name)
        except:
            print("Cannot import pettingzoo env: ", env_name)


args = get_args()

if args.ram:
    obs_type = 'ram'
else:
    obs_type = 'rgb_image'

# initialize the env
env = eval(args.env_name).parallel_env(obs_type=obs_type, full_action_space=False)
env_agents = env.unwrapped.agents  # this cannot go through supersuit wrapper, so get it first and reassign it

# assign necessary wrappers
if obs_type == 'rgb_image':
    env = supersuit.max_observation_v0(env, 2)  # as per openai baseline's MaxAndSKip wrapper, maxes over the last 2 frames to deal with frame flickering
    env = supersuit.color_reduction_v0(env, mode="B")
    env = supersuit.frame_skip_v0(env, 4) # skip frames for faster processing and less control to be compatable with gym, use frame_skip(env, (2,5))
    env = supersuit.resize_v1(env, 84, 84) # downscale observation for faster processing
    env = supersuit.frame_stack_v1(env, 4) # allow agent to see everything on the screen despite Atari's flickering screen problem
else:
    env = supersuit.frame_skip_v0(env, 4)  # RAM version also need frame skip, essential for boxing-v1, etc

# normalize the observation of Atari for both image or RAM 
env = supersuit.dtype_v0(env, 'float32') # need to transform uint8 to float first for normalizing observation: https://github.com/PettingZoo-Team/SuperSuit
env = supersuit.normalize_obs_v0(env, env_min=0, env_max=1) # normalize the observation to (0,1)
single_env = reward_lambda_v1(env, zero_sum_reward_filer)  # ensure zero-sum game
single_env.agents = env_agents
env = supersuit.pettingzoo_env_to_vec_env_v1(env)
env = supersuit.concat_vec_envs_v1(env, args.num_envs, num_cpus=0, base_class="gym")  # true number of envs will be args.num_envs
# env = gym.wrappers.RecordEpisodeStatistics(env)
if args.record_video:
    env.is_vector_env = True
    vec_env = gym.wrappers.RecordVideo(env, f"data/videos/{args.env_type}_{args.env_name}_{args.algorithm}",\
            step_trigger=lambda step: step % 10000 == 0, # record the videos every 10000 steps
            video_length=100)  # for each video record up to 100 steps) 

env.num_agents = single_env.num_agents
env.agents = single_env.agents
env = SSVecWrapper(env)

model = NashDQN(env, args)
rollout(env, model, args)