
import json
from typing import List
import numpy as np
import os
import random
from utils import *
from torch import nn

from tkinter import _flatten
from collections import Counter
class BinaryRecommendEnv(object):
    def __init__(self, kg, dataset, data_name, embed: str, seed=1, max_turn=15, cand_num=10, cand_item_num=10, attr_num=20, mode='train', ask_num=1, entropy_way='weight entropy', fm_epoch=0):
        '''
        :param kg: knowledge graph
        :param dataset: dataset name
        :param data_name: data name
        :param embed: embedding method, 'transe'
        :param seed: random seed
        :param fm_epoch: fm epoch, unused
        '''
        self.data_name = data_name
        self.mode = mode
        self.seed = seed
        self.max_turn = max_turn    #MAX_TURN
        self.attr_state_num = attr_num
        self.kg = kg
        self.dataset = dataset
        self.feature_length = getattr(self.dataset, 'feature').value_len
        self.user_length = getattr(self.dataset, 'user').value_len
        self.item_length = getattr(self.dataset, 'item').value_len

        # action parameters
        self.ask_num = ask_num
        self.rec_num = 10
        self.random_sample_feature = False
        self.random_sample_item = False
        if cand_num == 0:
            self.cand_num = 10
            self.random_sample_feature = True
        else:
            self.cand_num = cand_num
        if cand_item_num == 0:
            self.cand_item_num = 10
            self.random_sample_item = True
        else:
            self.cand_item_num = cand_item_num
        #  entropy  or weight entropy, what is weight entropy
        self.ent_way = entropy_way

        # user's profile
        self.reachable_feature = []   # user reachable feature
        self.user_acc_feature = []  # user accepted feature which asked by agent
        self.user_rej_feature = []  # user rejected feature which asked by agent
        self.cand_items = []   # candidate items
        self.item_feature_pair = {}
        self.cand_item_score = []

        #user_id  item_id   cur_step   cur_node_set
        self.user_id = None
        # target_item is the label !!!
        self.target_item = None
        self.cur_conver_step = 0        #  the number of conversation in current step
        self.cur_node_set = []     # maybe a node or a node set  /   normally save feature node
        # state veactor
        self.user_embed = None
        self.conver_his = []    #conversation_history
        self.attr_ent = []  # attribute entropy

        # user item interaction dict, for validation or test
        self.ui_dict = self.__load_rl_data__(data_name, mode=mode)  # np.array [ u i weight]
        self.user_weight_dict = dict()
        self.user_items_dict = dict()

        #init seed & init user_dict
        set_random_seed(self.seed) # set random seed
        if mode == 'train':
            self.__user_dict_init__() # init self.user_weight_dict  and  self.user_items_dict
        elif mode == 'test':
            self.ui_array = None    # u-i array [ [userID1, itemID1], ...,[userID2, itemID2]]
            self.__test_tuple_generate__()
            self.test_num = 0
        # embeds = {
        #     'ui_emb': ui_emb,
        #     'feature_emb': feature_emb
        # }
        # load fm epoch
        embeds = load_embed(data_name, embed, epoch=fm_epoch)
        if embeds:
            self.ui_embeds =embeds['ui_emb']
            self.feature_emb = embeds['feature_emb']
        else:
            # learn embedding?
            self.ui_embeds = nn.Embedding(self.user_length+self.item_length, 64).weight.data.numpy()
            self.feature_emb = nn.Embedding(self.feature_length, 64).weight.data.numpy()
        # self.feature_length = self.feature_emb.shape[0]-1

        self.action_space = 2

        # handcrafted reward, is there anyway to learn it?
        self.reward_dict = {
            'ask_suc': 0.01,
            'ask_fail': -0.1,
            'rec_suc': 1,
            'rec_fail': -0.1,
            'until_T': -0.3,      # MAX_Turn
            'cand_none': -0.1
        }
        self.history_dict = {
            'ask_suc': 1,
            'ask_fail': -1,
            'rec_scu': 2,
            'rec_fail': -2,
            'until_T': 0
        }
        self.attr_count_dict = dict()   # This dict is used to calculate entropy

    # data for validation and test
    def __load_rl_data__(self, data_name, mode):
        if mode == 'train':
            with open(os.path.join(DATA_DIR[data_name], 'UI_Interaction_data/review_dict_valid.json'), encoding='utf-8') as f:
                print('train_data: load RL valid data')
                mydict = json.load(f)
        elif mode == 'test':
            with open(os.path.join(DATA_DIR[data_name], 'UI_Interaction_data/review_dict_test.json'), encoding='utf-8') as f:
                print('test_data: load RL test data')
                mydict = json.load(f)
        return mydict


    def __user_dict_init__(self):   #Calculate the weight of the number of interactions per user
        '''
        the number of interactions per user devided by the number of all items
        '''
        ui_nums = 0
        for items in self.ui_dict.values():
            ui_nums += len(items)
        for user_str in self.ui_dict.keys():
            user_id = int(user_str)
            self.user_weight_dict[user_id] = len(self.ui_dict[user_str])/ui_nums
        print('user_dict init successfully!')

    def __test_tuple_generate__(self):
        '''
        generate test tuple (user_id, item_id) from self.ui_dict as self.ui_array (shuffled)
        '''
        ui_list = []
        for user_str, items in self.ui_dict.items():
            user_id = int(user_str)
            for item_id in items:
                ui_list.append([user_id, item_id])
        self.ui_array = np.array(ui_list)
        np.random.shuffle(self.ui_array)

    def reset(self, embed=None):
        '''
        somehow equivalent to __init__
        reset with embedding
        '''
        if embed is not None:
            self.ui_embeds = embed[:self.user_length+self.item_length]
            self.feature_emb = embed[self.user_length+self.item_length:]
        #init  user_id  item_id  cur_step   cur_node_set
        self.cur_conver_step = 0   #reset cur_conversation step

        # what is the meaning of cur_node_set?
        self.cur_node_set = []
        if self.mode == 'train':
            # randomly sample users and interacted items
            users = list(self.user_weight_dict.keys())
            # self.user_id = np.random.choice(users, p=list(self.user_weight_dict.values())) # select user  according to user weights
            self.user_id = np.random.choice(users)
            self.target_item = np.random.choice(self.ui_dict[str(self.user_id)])
        elif self.mode == 'test':
            # set user_id and target_item to column 0 and 1 of self.ui_array at row self.test_num
            self.user_id = self.ui_array[self.test_num, 0]
            self.target_item = self.ui_array[self.test_num, 1]
            self.test_num += 1

        # init user's profile,
        print('-----------reset state vector------------')
        print('user_id:{}, target_item:{}'.format(self.user_id, self.target_item))

        # P_{cand} ?
        self.reachable_feature = []  # user reachable feature in cur_step

        # P_u
        self.user_acc_feature = []  # user accepted feature which asked by agent

        # P_{rej}
        self.user_rej_feature = []  # user rejected feature which asked by agent

        # V_{cand}
        self.cand_items = list(range(self.item_length))

        # init state vector
        self.user_embed = self.ui_embeds[self.user_id].tolist()  # init user_embed   np.array---list
        self.conver_his = [0] * self.max_turn  # conversation_history
        self.attr_ent = [0] * self.attr_state_num  # attribute entropy

        # initialize dialog by randomly asked a question from ui interaction
        # an random attribute of the target item?
        user_like_random_fea = random.choice(self.kg.G['item'][self.target_item]['belong_to'])
        self.user_acc_feature.append(user_like_random_fea) #update user acc_fea
        self.cur_node_set.append(user_like_random_fea)

        # by default regard the first random attribute as accepted
        # cand_score updated inside
        self._update_cand_items(user_like_random_fea, acc_rej=True)
        self._updata_reachable_feature()  # self.reachable_feature = []

        # conversation history, update the reward
        self.conver_his[self.cur_conver_step] = self.history_dict['ask_suc']
        # ? rewards stored in conver_his ?
        self.cur_conver_step += 1

        print('=== init user prefer feature: {}'.format(self.cur_node_set))
        self._update_feature_entropy()  #update entropy

        # reachable_feature resetted when self._update_reachable_feature()
        print('reset_reachable_feature num: {}'.format(len(self.reachable_feature)))

        # Sort reachable features according to the entropy of features
        reach_fea_score = self._feature_score()
        max_ind_list = []

        # get max cand_num reach_fea_score 's index
        for k in range(self.cand_num):
            max_score = max(reach_fea_score)
            max_ind = reach_fea_score.index(max_score)
            reach_fea_score[max_ind] = 0
            if max_ind in max_ind_list:
                break
            max_ind_list.append(max_ind)
        
        max_fea_id = [self.reachable_feature[i] for i in max_ind_list]

        # move top score feature to head # append to head of reachable_feature according to feature score descending
        [self.reachable_feature.remove(v) for v in max_fea_id]
        [self.reachable_feature.insert(0, v) for v in max_fea_id[::-1]]

        # return the 
        return self._get_state(), self._get_cand(), self._get_action_space()

    def _get_cand(self):
        if self.random_sample_feature:
            cand_feature = self._map_to_all_id(random.sample(self.reachable_feature, min(len(self.reachable_feature),self.cand_num)),'feature')
        else:
            cand_feature = self._map_to_all_id(self.reachable_feature[:self.cand_num],'feature')
        if self.random_sample_item:
            cand_item =  self._map_to_all_id(random.sample(self.cand_items, min(len(self.cand_items),self.cand_item_num)),'item')
        else:
            cand_item = self._map_to_all_id(self.cand_items[:self.cand_item_num],'item')
        cand = cand_feature + cand_item
        return cand
    
    def _get_action_space(self) -> List[List]:
        '''
        return list 0: list of embedding id of reachable_feature
        return list 1: list of embedding id of candidates
        '''
        action_space = [self._map_to_all_id(self.reachable_feature,'feature'), self._map_to_all_id(self.cand_items,'item')]
        return action_space

    def _get_state(self):
        if self.data_name in ['YELP_STAR']:
            self_cand_items = self.cand_items[:5000]
            set_cand_items = set(self_cand_items)
        else:
            self_cand_items = self.cand_items        
        user = [self.user_id]
        cur_node = [x + self.user_length + self.item_length for x in self.cur_node_set] 
        cand_items = [x + self.user_length for x in self_cand_items]
        reachable_feature = [x + self.user_length + self.item_length for x in self.reachable_feature]
        neighbors = cur_node + user + cand_items + reachable_feature
        
        idx = dict(enumerate(neighbors))
        idx = {v: k for k, v in idx.items()}

        i = []
        v = []
        for item in self_cand_items:
            item_idx = item + self.user_length
            for fea in self.item_feature_pair[item]:
                fea_idx = fea + self.user_length + self.item_length
                i.append([idx[item_idx], idx[fea_idx]])
                i.append([idx[fea_idx], idx[item_idx]])
                v.append(1)
                v.append(1)

        user_idx = len(cur_node)
        cand_item_score = self.sigmoid(self.cand_item_score)
        for item, score in zip(self.cand_items, cand_item_score):
            if self.data_name in ['YELP_STAR']:
                if item not in set_cand_items:
                    continue
            item_idx = item + self.user_length
            i.append([user_idx, idx[item_idx]])
            i.append([idx[item_idx], user_idx])
            v.append(score)
            v.append(score)
        
        i = torch.LongTensor(i)
        v = torch.FloatTensor(v)
        neighbors = torch.LongTensor(neighbors)
        adj = torch.sparse.FloatTensor(i.t(), v, torch.Size([len(neighbors),len(neighbors)]))

        state = {'cur_node': cur_node,
                 'neighbors': neighbors,
                 'adj': adj}
        return state
    
    def step(self, action, sorted_actions, embed=None):  
        if embed is not None:
            self.ui_embeds = embed[:self.user_length+self.item_length]
            self.feature_emb = embed[self.user_length+self.item_length:]

        done = 0
        print('---------------step:{}-------------'.format(self.cur_conver_step))

        if self.cur_conver_step == self.max_turn:
            reward = self.reward_dict['until_T']
            self.conver_his[self.cur_conver_step-1] = self.history_dict['until_T']
            print('--> Maximum number of turns reached !')
            done = 1
        # action is the index in ui_embeds ?.
        elif action >= self.user_length + self.item_length:   #ask feature
            asked_feature = self._map_to_old_id(action)
            print('-->action: ask features {}, max entropy feature {}'.format(asked_feature, self.reachable_feature[:self.cand_num]))
            reward, done, acc_rej = self._ask_update(asked_feature)  #update user's profile:  user_acc_feature & user_rej_feature
            self._update_cand_items(asked_feature, acc_rej)   #update cand_items
        else:  #recommend items
            
            #===================== rec update=========
            recom_items = []
            for act in sorted_actions:
                if act < self.user_length + self.item_length:
                    recom_items.append(self._map_to_old_id(act))
                    if len(recom_items) == self.rec_num:
                        break
            reward, done = self._recommend_update(recom_items)
            #========================================
            if reward > 0:
                print('-->Recommend successfully!')
            else:
                print('-->Recommend fail !')
        
        self._updata_reachable_feature()  # update user's profile: reachable_feature

        print('reachable_feature num: {}'.format(len(self.reachable_feature)))
        print('cand_item num: {}'.format(len(self.cand_items)))

        self._update_feature_entropy()
        if len(self.reachable_feature) != 0:  # if reachable_feature == 0 :cand_item= 1
            reach_fea_score = self._feature_score()  # compute feature score

            max_ind_list = []
            for k in range(self.cand_num):
                max_score = max(reach_fea_score)
                max_ind = reach_fea_score.index(max_score)
                reach_fea_score[max_ind] = 0
                if max_ind in max_ind_list:
                    break
                max_ind_list.append(max_ind)
            max_fea_id = [self.reachable_feature[i] for i in max_ind_list]
            [self.reachable_feature.remove(v) for v in max_fea_id]
            [self.reachable_feature.insert(0, v) for v in max_fea_id[::-1]]

        self.cur_conver_step += 1
        return self._get_state(), self._get_cand(), self._get_action_space(), reward, done


    def _updata_reachable_feature(self):
        # reachable feature is candidate feature? no, also includes user rejected feature and already accepted feature
        next_reachable_feature = []
        reachable_item_feature_pair = {}
        for cand in self.cand_items:
            # the candidate item's features
            # all candidates' features should be included in reachable_feature
            fea_belong_items = list(self.kg.G['item'][cand]['belong_to']) # A-I
            next_reachable_feature.extend(fea_belong_items)
            reachable_item_feature_pair[cand] = list(set(fea_belong_items) - set(self.user_rej_feature))
            next_reachable_feature = list(set(next_reachable_feature))

        # self.reachable_feature is the candidate feature!
        self.reachable_feature = list(set(next_reachable_feature) - set(self.user_acc_feature) - set(self.user_rej_feature))
        self.item_feature_pair = reachable_item_feature_pair

    def _feature_score(self):
        reach_fea_score = []
        for feature_id in self.reachable_feature:
            '''
            score = self.attr_ent[feature_id]
            reach_fea_score.append(score)
            '''
            feature_embed = self.feature_emb[feature_id]
            score = 0
            score += np.inner(np.array(self.user_embed), feature_embed)
            prefer_embed = self.feature_emb[self.user_acc_feature, :]  #np.array (x*64)
            for i in range(len(self.user_acc_feature)):
                score += np.inner(prefer_embed[i], feature_embed)
            if feature_id in self.user_rej_feature:
                score -= self.sigmoid([feature_embed, feature_embed])[0]
            reach_fea_score.append(score)
            
        return reach_fea_score


    def _item_score(self):
        '''
        w_v^{(t)} = \sigmoid(e_u^{T} * e_v + \sigma_{p \in P_u^{(t)}} e_v^{T} * e_p - \sigma_{p \in P_{rej}^{(t)} \interact P_v^{(t)} } e_v^{T} * e_p)
        '''
        cand_item_score = []
        # can this part be optimized with matrix operation?
        for item_id in self.cand_items:
            # self.user_length is some kind of offset here
            item_embed = self.ui_embeds[self.user_length + item_id]
            score = 0
            # why not put them all on GPU ?
            score += np.inner(np.array(self.user_embed), item_embed)
            # self.user_acc_feature is random selected when resetting the env
            prefer_embed = self.feature_emb[self.user_acc_feature, :]  #np.array (x*64)
            unprefer_feature = list(set(self.user_rej_feature) & set(self.kg.G['item'][item_id]['belong_to']))
            unprefer_embed = self.feature_emb[unprefer_feature, :]  #np.array (x*64)
            for i in range(len(self.user_acc_feature)):
                score += np.inner(prefer_embed[i], item_embed)
            for i in range(len(unprefer_feature)):
                score -= self.sigmoid([np.inner(unprefer_embed[i], item_embed)])[0]
                #score -= np.inner(unprefer_embed[i], item_embed)
            cand_item_score.append(score)
        return cand_item_score 


    def _ask_update(self, asked_feature):
        '''
        :return: reward, acc_feature, rej_feature
        '''
        done = 0
        # TODO datafram!     groundTruth == target_item features
        feature_groundtrue = self.kg.G['item'][self.target_item]['belong_to']

        if asked_feature in feature_groundtrue:
            acc_rej = True
            self.user_acc_feature.append(asked_feature)
            self.cur_node_set.append(asked_feature)
            reward = self.reward_dict['ask_suc']
            self.conver_his[self.cur_conver_step] = self.history_dict['ask_suc']   #update conver_his
        else:
            acc_rej = False
            self.user_rej_feature.append(asked_feature)
            reward = self.reward_dict['ask_fail']
            self.conver_his[self.cur_conver_step] = self.history_dict['ask_fail']  #update conver_his

        if self.cand_items == []:  #candidate items is empty
            done = 1
            reward = self.reward_dict['cand_none']

        return reward, done, acc_rej

    def _update_cand_items(self, asked_feature, acc_rej):
        if acc_rej:    # accept feature
            print('=== ask acc: update cand_items')
            # feature.belong_to is list of all the items that belong to this feature
            feature_items = self.kg.G['feature'][asked_feature]['belong_to']

            # cand_items expanded
            self.cand_items = set(self.cand_items) & set(feature_items)   #  itersection
            self.cand_items = list(self.cand_items)
        
        else:    # reject feature
            print('=== ask rej: update cand_items')
        
        #select topk candidate items to recommend
        # item_score updated here !
        cand_item_score = self._item_score()
        item_score_tuple = list(zip(self.cand_items, cand_item_score))

        # sorted by score in descending order
        sort_tuple = sorted(item_score_tuple, key=lambda x: x[1], reverse=True)
        self.cand_items, self.cand_item_score = zip(*sort_tuple)
    
    def _recommend_update(self, recom_items):
        print('-->action: recommend items')
        print(set(recom_items) - set(self.cand_items[: self.rec_num]))
        self.cand_items = list(self.cand_items)
        self.cand_item_score = list(self.cand_item_score)
        #recom_items = self.cand_items[: self.rec_num]    # TOP k item to recommend
        if self.target_item in recom_items:
            reward = self.reward_dict['rec_suc'] 
            self.conver_his[self.cur_conver_step] = self.history_dict['rec_scu'] #update state vector: conver_his
            tmp_score = []
            for item in recom_items:
                idx = self.cand_items.index(item)
                tmp_score.append(self.cand_item_score[idx])
            self.cand_items = recom_items
            self.cand_item_score = tmp_score
            done = recom_items.index(self.target_item) + 1
        else:
            reward = self.reward_dict['rec_fail']
            self.conver_his[self.cur_conver_step] = self.history_dict['rec_fail']  #update state vector: conver_his
            if len(self.cand_items) > self.rec_num:
                for item in recom_items:
                    del self.item_feature_pair[item]
                    idx = self.cand_items.index(item)
                    self.cand_items.pop(idx)
                    self.cand_item_score.pop(idx)
                #self.cand_items = self.cand_items[self.rec_num:]  #update candidate items
            done = 0
        return reward, done

    def _update_feature_entropy(self):
        if self.ent_way == 'entropy':
            cand_items_fea_list = []
            for item_id in self.cand_items:
                cand_items_fea_list.append(list(self.kg.G['item'][item_id]['belong_to']))
            cand_items_fea_list = list(_flatten(cand_items_fea_list))

            # Counter
            self.attr_count_dict = dict(Counter(cand_items_fea_list))

            # attribute entropy
            self.attr_ent = [0] * self.attr_state_num  # reset attr_ent

            # reachable_feature is increasing, but real_ask_able only consider the features 
            # related to self.cand_items
            real_ask_able = list(set(self.reachable_feature) & set(self.attr_count_dict.keys()))
            for fea_id in real_ask_able:
                p1 = float(self.attr_count_dict[fea_id]) / len(self.cand_items)
                p2 = 1.0 - p1
                if p1 == 1:
                    self.attr_ent[fea_id] = 0
                else:
                    ent = (- p1 * np.log2(p1) - p2 * np.log2(p2))
                    self.attr_ent[fea_id] = ent
        elif self.ent_way == 'weight_entropy':
            cand_items_fea_list = []
            self.attr_count_dict = {}
            #cand_item_score = self._item_score()
            cand_item_score_sig = self.sigmoid(self.cand_item_score)  # sigmoid(score)

            # score_ind is index
            for score_ind, item_id in enumerate(self.cand_items):
                # there is no flatten operation here, feature list for each candidate item
                cand_items_fea_list = list(self.kg.G['item'][item_id]['belong_to'])
                for fea_id in cand_items_fea_list:

                    # append to attr_count_dict
                    # ??? use Counter here ???
                    # the same operation as in 'entropy' !!!
                    if self.attr_count_dict.get(fea_id) == None:
                        self.attr_count_dict[fea_id] = 0
                    self.attr_count_dict[fea_id] += cand_item_score_sig[score_ind]

            self.attr_ent = [0] * self.attr_state_num  # reset attr_ent
            real_ask_able = list(set(self.reachable_feature) & set(self.attr_count_dict.keys()))


            # difference is here, the weight is not the same as in 'entropy'
            sum_score_sig = sum(cand_item_score_sig)

            for fea_id in real_ask_able:
                p1 = float(self.attr_count_dict[fea_id]) / sum_score_sig    # score_sig is sigmoid(score)
                p2 = 1.0 - p1
                if p1 == 1 or p1 <= 0:
                    self.attr_ent[fea_id] = 0
                else:
                    ent = (- p1 * np.log2(p1) - p2 * np.log2(p2))
                    self.attr_ent[fea_id] = ent

    def sigmoid(self, x_list):
        x_np = np.array(x_list)
        s = 1 / (1 + np.exp(-x_np))
        return s.tolist()

    def _map_to_all_id(self, x_list, old_type):
        if old_type == 'item':
            return [x + self.user_length for x in x_list]
        elif old_type == 'feature':
            return [x + self.user_length + self.item_length for x in x_list]
        else:
            return x_list

    def _map_to_old_id(self, x):
        if x >= self.user_length + self.item_length:
            x -= (self.user_length + self.item_length)
        elif x >= self.user_length:
            x -= self.user_length
        return x

