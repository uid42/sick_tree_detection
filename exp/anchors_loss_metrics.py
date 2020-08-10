
#################################################
### THIS FILE WAS AUTOGENERATED! DO NOT EDIT! ###
#################################################
# file to edit: dev_nb/anchors_loss_metrics.ipynb

#================================================
import sys
sys.path.append('../')
from exp import resnet_ssd


#================================================
from FLAI.detect_symbol.exp import databunch
#from detect_symbol.exp import nb_init_model
from FLAI.detect_symbol.exp import resnet_ssd as resnet_ssd_detsym
from FLAI.detect_symbol.exp import anchors_loss_metrics as anchors_loss_metrics_detsym


#================================================
import torch


#================================================
from torch.nn import functional as F


#================================================
from torch import tensor


#================================================
from IPython.core import debugger as idb


#================================================
import numpy as np


#================================================
import math


#================================================
def find_neibs(idx, grids = (49, 49), dis = 1):
    '''
    找到某个anchor周围相邻的anchors的下标里列表。距离默认1。
    这个任务中只有第一层的grids参与，所以只需要第一次的grids的尺寸。
    anchor也是1对1的。
    参数：
        idx：目标anchor在grid anchors(get_grid_anchors返回的gvs)列表中的下标
        grids: 尺寸
        dis：邻居的距离
    返回值：
        邻居的下标列表
    '''
    gh, gw = grids
    x = idx % gh
    y = idx // gw
    ret = []
    for nx in range(x - dis, x + dis + 1):
        for ny in range(y - dis, y + dis + 1):
            if nx >= 0 and ny >= 0 and nx < gw and ny < gh \
                    and not(nx == x and ny == y):
                nidx = ny * gw + nx
                ret += [nidx]
    return ret




#================================================
#定义一个新的GridAnchor_Functions，主要是修改:
#get_scroe_hits->get_hits,b2t->b2c,t2b->c2b;
class GridAnchor_Funcs(anchors_loss_metrics_detsym.GridAnchor_Funcs):
    def __init__(self, fig_hw, grids, device):
        anchors = [[(0, 0)]]
        gvs,ghs,gws,avs,ahs,aws = anchors_loss_metrics_detsym.get_grids_anchors( \
                    fig_hw, grids, anchors)
        self.grids = grids
        super().__init__(gvs, avs, device)

    #下面的三个函数都用不上了。防止被调用到。
    def get_scores_hits(self, gt_bboxs):
        assert False, 'deleted'
    def b2t(self, gt_bboxs,idx,eps=1):
        assert False, 'deleted'
    def t2b(self,t,idx,eps=1):
        assert False, 'deleted'

    def get_hits(self, gt_bboxs):
        # ground truch bbox center x,y
        gt_cx = gt_bboxs[:,[0,2]].mean(-1)
        gt_cy = gt_bboxs[:,[1,3]].mean(-1)

        # 判断目标bbox的中心落在哪个cell内
        hits = ((gt_cx[:,None] >= self.gvs[:,0][None]) &
                (gt_cx[:,None] <  self.gvs[:,2][None]) &
                (gt_cy[:,None] >= self.gvs[:,1][None]) &
                (gt_cy[:,None] <  self.gvs[:,3][None]))

        return hits

    def b2c(self, gt_bboxs,idx,eps=1):
        '''
        gt_bbox->center
        '''
        cx,cy = self.gvs[idx,0],self.gvs[idx,1]
        gh,gw = self.ghs[idx],self.gws[idx]
        #ph,pw = self.ahs[idx],self.aws[idx]

        bx = (gt_bboxs[:,0] + gt_bboxs[:,2])/2 # x of center of box
        by = (gt_bboxs[:,1] + gt_bboxs[:,3])/2 # y of center of box
        hatsig_tx = (bx - cx)/gh
        hatsig_ty = (by - cy)/gw

        sig_tx = (hatsig_tx+0.5*eps)/(1+eps)
        sig_ty = (hatsig_ty+0.5*eps)/(1+eps)

        tx = torch.log(sig_tx/(1-sig_tx))
        ty = torch.log(sig_ty/(1-sig_ty))

        return torch.stack([tx, ty]).t()

    def c2b(self,t,idx,eps=1):
        '''
        center->bbox.这些bbox都是没有宽高的。也就是后右下角坐标和左上角坐标相同。
        '''
        cx,cy = self.gvs[idx,0],self.gvs[idx,1]
        gh,gw = self.ghs[idx],self.gws[idx]

        sig_tx = torch.sigmoid(t[...,0])
        sig_ty = torch.sigmoid(t[...,1])

        hatsig_tx = (1+eps)*(sig_tx-0.5) + 0.5
        hatsig_ty = (1+eps)*(sig_ty-0.5) + 0.5

        bx = hatsig_tx*gw + cx # x of center of box
        by = hatsig_ty*gh + cy # y of center of box

        res = torch.stack([bx, by, bx, by],dim=0)
        res = res.permute(list(range(len(res.shape)))[1:]+[0])
        return res



#================================================
def clas_acc(pred_batch, *gt_batch, gaf):
    '''
    classification accuracy
    '''
    posCnt = tensor(0.)
    totCnt = tensor(0.)
    for pred_clas,gt_bboxs,gt_clas in zip(pred_batch[2], *gt_batch):
        keep = anchors_loss_metrics_detsym.get_y(gt_bboxs)
        if keep.numel()==0: continue

        gt_bboxs = gt_bboxs[keep]
        gt_clas = gt_clas[keep]

        gt_bboxs = (gt_bboxs + 1) / 2
        gt_clas = gt_clas - 1 # the databunch add a 'background' class to classes[0], but we don't want that,so gt_clas-1

        hits = gaf.get_hits(gt_bboxs)
        idx = idx_fromHits(hits)

        pred_clas = pred_clas[idx]
        pred_clas = pred_clas.max(1)[1]

        posCnt += (pred_clas==gt_clas).sum().item()
        totCnt += gt_clas.shape[0]

    return posCnt/totCnt


#================================================
def clas_L(pred_batch, *gt_batch, lambda_clas=1, clas_weights=None, gaf):
    '''
    class loss
    若某anchor对某object负责，则应训练其classification靠近该object的类别。
    '''
    loss = 0
    cnt = 0
    for pred_clas,gt_bboxs,gt_clas in zip(pred_batch[2], *gt_batch):
        keep = anchors_loss_metrics_detsym.get_y(gt_bboxs)
        if keep.numel()==0: continue

        gt_bboxs = gt_bboxs[keep]
        gt_clas = gt_clas[keep]

        gt_bboxs = (gt_bboxs + 1) / 2
        gt_clas = gt_clas - 1 # the databunch add a 'background' class to classes[0], but we don't want that,so gt_clas-1

        hits = gaf.get_hits(gt_bboxs)
        idx = idx_fromHits(hits)

        pred_clas = pred_clas[idx]

        loss += F.cross_entropy(pred_clas, gt_clas, weight=clas_weights, reduction='sum')
        cnt += gt_clas.shape[0]

    return lambda_clas*loss/cnt


#================================================
def cent_L(pred_batch, *gt_batch, lambda_cent=1, clas_weights=None, gaf):
    '''
    bbox center loss
    若某 anchor 对某 object 负责，则应训练其预测之 中心 靠近该 object box 之 中心。
    '''
    loss = 0
    cnt = 0
    for pred_txy,gt_bboxs,gt_clas in zip(pred_batch[0], *gt_batch):
        keep = anchors_loss_metrics_detsym.get_y(gt_bboxs)
        if keep.numel()==0: continue

        gt_bboxs = gt_bboxs[keep]
        gt_clas = gt_clas[keep]

        gt_bboxs = (gt_bboxs + 1) / 2
        gt_clas = gt_clas - 1

        if clas_weights is not None: ws = clas_weights[gt_clas]
        else: ws = None

        hits = gaf.get_hits(gt_bboxs)
        idx = idx_fromHits(hits)

        gt_t = gaf.b2c(gt_bboxs,idx,eps=1)
        pred_txy = pred_txy[idx]

        if ws is not None:
            tmp = ((gt_t[...,:2]-pred_txy)*ws[...,None]).abs().sum()
        else:
            tmp = (gt_t[...,:2]-pred_txy).abs().sum()

        loss += tmp
        cnt += len(idx)

    return lambda_cent*loss/cnt


#================================================
def pConf_L(pred_batch, *gt_batch, lambda_pconf=1, clas_weights=None, gaf):
    '''
    positive confidence loss
    若某 anchor 为某 object 负责，则训练其 conf_score 靠近 1。
    '''
    loss = 0
    cnt = 0
    for pred_conf,gt_bboxs,gt_clas in zip(pred_batch[1], *gt_batch):
        keep = anchors_loss_metrics_detsym.get_y(gt_bboxs)
        if keep.numel()==0: continue

        gt_bboxs = gt_bboxs[keep]
        gt_clas = gt_clas[keep]

        gt_bboxs = (gt_bboxs + 1) / 2
        gt_clas = gt_clas - 1

        if clas_weights is not None: ws = clas_weights[gt_clas]
        else: ws = None

        hits = gaf.get_hits(gt_bboxs)
        idx = idx_fromHits(hits)

        conf_pos = pred_conf[idx]
#         conf_pos = torch.sigmoid(conf_pos)
#         tmp = (1-conf_pos).abs().sum()
        if ws is not None:
            tmp = F.binary_cross_entropy_with_logits(conf_pos,torch.ones_like(conf_pos),weight=ws[...,None],reduction='sum')
        else:
            tmp = F.binary_cross_entropy_with_logits(conf_pos,torch.ones_like(conf_pos),reduction='sum')


        loss += tmp
        cnt += len(idx)

    return lambda_pconf*loss/cnt


#================================================
def nConf_L(pred_batch, *gt_batch, gaf, conf_th=0.5, lambda_nconf=1):
    '''
    negative confidence loss
    若某 anchor 不对任何 object 负责，且它与任何 object 的 匹配得分 都差于 threshold，则训练其 conf_score 靠近 0。
    '''
    loss = 0
    cnt = 0
    for pred_conf,gt_bboxs,_ in zip(pred_batch[1], *gt_batch):
        keep = anchors_loss_metrics_detsym.get_y(gt_bboxs)
        if keep.numel()==0: continue

        gt_bboxs = gt_bboxs[keep]
        gt_bboxs = (gt_bboxs + 1) / 2

        hits = gaf.get_hits(gt_bboxs)
        idx = idx_fromHits(hits)

        #positive
        tmp = (hits * 1).max(dim=0)[0]

        #取得命中的anchor周围的anchor的下标立标
        discards = []
        for hidx in idx:
            neibs = find_neibs(hidx, gaf.grids[0], dis = 1)
            for i in neibs:
                discards += [i]
        #把周围的邻居加进来，剩下的就是negative了
        tmp[discards] = 1

        neg_idx = torch.where(tmp==0)[0] # 如果没有，该anchor是negative anchor

        conf_neg = pred_conf[neg_idx]
#         conf_neg = torch.sigmoid(conf_neg)
#         loss += conf_neg.abs().sum()
        tmp = F.binary_cross_entropy_with_logits(conf_neg,torch.zeros_like(conf_neg),reduction='sum')
        loss += tmp
        cnt += len(neg_idx)

    return lambda_nconf*loss/cnt


#================================================
def yolo_L(pred_batch, *gt_batch, conf_th=0.5,
           lambda_cent=1, lambda_pconf=1, lambda_nconf=1, lambda_clas=1, clas_weights=None, gaf):
    '''
    与detect_symbol里面的yolo_L相比的区别是：
        不计算宽高方面的损失
        neg_idx要去掉find_neibs返回的discard列表

    clas_weights:
    为了解决数据集的imbalance问题，一种方法是在dataloader中使用WeightedRandomSampler，但是这种方法不适用于目标检测问题。
    因为，（1）目标检测的label不是一个简单的数值（2）目标检测问题的一张图片可能包括不同类别的多个目标。
    所以为了解决目标检测问题中的imbalance问题，我们的方法是在损失函数中使用权重。
    为各类别分配权重，各目标对应的损失乘以该目标所属类别的权重。
    默认为None，即不使用权重。
    若设置非None，则clas_weights应该是一个一维tensor，其长度等于数据集的类别数。
    若设置为全1，则相当于不使用权重。
    合理的设置应保证所有元素之和等于数据集的类别数，否则相当于对损失函数的整体做了缩放。
    '''
    clas_loss = 0
    cent_loss = 0
    pconf_loss = 0
    nconf_loss = 0
    pos_cnt = 0
    neg_cnt = 0

    for pred_txy,pred_conf,pred_clas,gt_bboxs,gt_clas in zip(*pred_batch, *gt_batch):
        keep = anchors_loss_metrics_detsym.get_y(gt_bboxs)
        if keep.numel()==0:
            #这时候所有anchor都是negative的。所以空白的也要贡献自己的loss
            conf_neg = pred_conf#所有anchor的
            nconf_loss += F.binary_cross_entropy_with_logits(conf_neg,torch.zeros_like(conf_neg),reduction='sum')
            neg_cnt += len(pred_conf)
            continue

        gt_bboxs = gt_bboxs[keep]
        gt_clas = gt_clas[keep]

        gt_bboxs = (gt_bboxs + 1) / 2
        gt_clas = gt_clas - 1 # the databunch add a 'background' class to classes[0], but we don't want that,so gt_clas-1

        if clas_weights is not None: ws = clas_weights[gt_clas]
        else: ws = None

        hits = gaf.get_hits(gt_bboxs)
        idx = idx_fromHits(hits)

        # classification loss
        pred_clas = pred_clas[idx]
        clas_loss += F.cross_entropy(pred_clas, gt_clas, weight=clas_weights, reduction='sum')

        # bbox center loss
        gt_t = gaf.b2c(gt_bboxs,idx,eps=1)
        pred_txy = pred_txy[idx]
        if ws is not None:
            cent_loss += ((gt_t[...,:2]-pred_txy)*ws[...,None]).abs().sum()
        else:
            cent_loss += (gt_t[...,:2]-pred_txy).abs().sum()

        # positive confidence loss
        conf_pos = pred_conf[idx]
        if ws is not None:
            pconf_loss += F.binary_cross_entropy_with_logits(conf_pos,torch.ones_like(conf_pos),weight=ws[...,None],reduction='sum')
        else:
            pconf_loss += F.binary_cross_entropy_with_logits(conf_pos,torch.ones_like(conf_pos),reduction='sum')

        #positive
        tmp = (hits * 1).max(dim=0)[0]

        #取得命中的anchor周围的anchor的下标立标
        discards = []
        for hidx in idx:
            neibs = find_neibs(hidx, gaf.grids[0], dis = 1)
            for i in neibs:
                discards += [i]
        #把周围的邻居加进来，剩下的就是negative了
        tmp[discards] = 1

        neg_idx = torch.where(tmp==0)[0] # 如果没有，该anchor是negative anchor

        conf_neg = pred_conf[neg_idx]
        nconf_loss += F.binary_cross_entropy_with_logits(conf_neg,torch.zeros_like(conf_neg),reduction='sum')

        pos_cnt += len(idx)
        neg_cnt += len(neg_idx)


    if pos_cnt > 0:#测试的极端情况碰到都是空白的。只有nconf_loss在前面计算了。
        clas_loss  = lambda_clas  * clas_loss  /pos_cnt
        cent_loss  = lambda_cent  * cent_loss  /pos_cnt
        pconf_loss = lambda_pconf * pconf_loss /pos_cnt
    nconf_loss = lambda_nconf * nconf_loss /neg_cnt

    return clas_loss + cent_loss + pconf_loss + nconf_loss




#================================================
def bbox2c(b):
    '''
    将bbox的（左上x，左上y，右下x，右下y）表示变为（中心x，中心y）表示
    '''
    cx = b[...,[0,2]].mean(-1)[...,None]
    cy = b[...,[1,3]].mean(-1)[...,None]

    return torch.cat([cx,cy],dim=-1)


#================================================
def idx_fromHits(hits):
    idx = (hits * 1).max(1)[1]
    return idx


#================================================
def cent_d(pred_batch, *gt_batch, gaf):
    '''
    bbox center difference
    '''
    dif = tensor(0.)
    cnt = tensor(0.)
    for pred_txy,gt_bboxs,_ in zip(pred_batch[0], *gt_batch):
        keep = anchors_loss_metrics_detsym.get_y(gt_bboxs)
        if keep.numel()==0: continue

        #pred_t = torch.cat([pred_txy,pred_thw],dim=1)
        pred_t = pred_txy

        gt_bboxs = gt_bboxs[keep]
        gt_bboxs = (gt_bboxs + 1) / 2

        hits = gaf.get_hits(gt_bboxs)
        idx = idx_fromHits(hits)

        pred_t = pred_t[idx]
        pred_c = bbox2c(gaf.c2b(pred_t,idx))[...,:2]
        gt_c = bbox2c(gt_bboxs)[...,:2]

        tmp = (gt_c - pred_c).abs().sum()
        dif += tmp
        cnt += len(idx)

    return dif/cnt/2