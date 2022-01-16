# [rotem]: unused imports that were originally in code
# import collections
# import os, sys
# import torch.nn.functional as F

from dataset import dataset_processing
from model.resnet50 import resnet50
import numpy as np
import os
import time
from timeit import default_timer as timer
import torch
import torch.backends.cudnn as cudnn
import torch.nn as nn
from torch.utils.data import DataLoader
from torchvision import transforms
from transforms.affine_transforms import RandomRotate
from utils.genLD import gen_label_distribution
from utils.report import report_metrics, report_mae_mse
from utils.utils import Logger, AverageMeter, time_to_str, weights_init
import warnings

warnings.filterwarnings("ignore")
os.environ["CUDA_VISIBLE_DEVICES"] = "1"

# Set random seed for reproducibility
np.random.seed(42)

# Hyper Parameters
BATCH_SIZE      = 16
BATCH_SIZE_TEST = 20
LR              = 0.001                 # learning rate
NUM_WORKERS     = 1
NUM_CLASSES     = 4
lr_steps        = [30, 60, 90, 120]     # adjust the learning rate at these epoch

# [rotem:] original path to data
# DATA_PATH = '/home/ubuntu5/wxp/datasets/acne4/VOCdevkit2007/VOC2007/JPEGImages_300'

DATA_PATH = '/host_root/fastData/DataSets/Acne04/Classification/JPEGImages'

LOG_DIR   = '/host_root/home/rotem/Private/Academic/LDL-rotem/logs'
time_now  = time.strftime("%Y-%m-%d_%H:%M:%S", time.localtime())
LOG_NAME  = f'log_{time_now}.log'
LOG_PATH  = os.path.join(LOG_DIR, LOG_NAME)

log = Logger()
log.open(LOG_PATH)

def criterion(lesions_num):
    if lesions_num <= 5:
        return 0
    elif lesions_num <= 20:
        return 1
    elif lesions_num <= 50:
        return 2
    else:
        return 3


def adjust_learning_rate_new(optimizer, decay=0.5):
    """ Sets the learning rate to the initial LR decayed by 0.5 every 20 epochs """
    for param_group in optimizer.param_groups:
        param_group['lr'] = decay * param_group['lr']


# init normalization transform to be applied to tr/ts data
normalize = transforms.Normalize(mean=[0.45815152, 0.361242, 0.29348266], std=[0.2814769, 0.226306, 0.20132513])

# chain together transformations to be applied to train data
tr_transforms = transforms.Compose([transforms.Scale((256, 256)), transforms.RandomCrop(224), transforms.RandomHorizontalFlip(),
                                    transforms.ToTensor(), RandomRotate(rotation_range=20), normalize])

# chain together transformations to be applied to test data
ts_transforms = transforms.Compose([transforms.Scale((224, 224)), transforms.ToTensor(), normalize])


def trainval_test(cross_val_index, sigma, lamda):

    TRAIN_FILE = '/host_root/fastData/DataSets/Acne04/Detection/VOC2007/ImageSets/Main/NNEW_trainval_' + cross_val_index + '.txt'
    dset_train = dataset_processing.DatasetProcessing(DATA_PATH, TRAIN_FILE, transform=tr_transforms)
    train_loader = DataLoader(dset_train, batch_size=BATCH_SIZE, shuffle=True, num_workers=NUM_WORKERS, pin_memory=True)

    TEST_FILE = '/host_root/fastData/DataSets/Acne04/Detection/VOC2007/ImageSets/Main/NNEW_test_' + cross_val_index + '.txt'
    dset_test = dataset_processing.DatasetProcessing(DATA_PATH, TEST_FILE, transform=ts_transforms)
    test_loader = DataLoader(dset_test, batch_size=BATCH_SIZE_TEST, shuffle=False, num_workers=NUM_WORKERS, pin_memory=True)

    cnn = resnet50().cpu()

    cudnn.benchmark = True

    params = []
    for key, value in dict(cnn.named_parameters()).items():
        if value.requires_grad:
            if any(name in key for name in ['fc', 'counting']):
                params += [{'params': [value], 'lr': LR * 1.0, 'weight_decay': 5e-4}]
            else:
                params += [{'params': [value], 'lr': LR * 1.0, 'weight_decay': 5e-4}]

    optimizer = torch.optim.SGD(params, momentum=0.9)

    loss_func = nn.CrossEntropyLoss().cpu()
    kl_loss_1 = nn.KLDivLoss().cpu()
    kl_loss_2 = nn.KLDivLoss().cpu()
    kl_loss_3 = nn.KLDivLoss().cpu()

    # training and testing
    start = timer()
    test_acc_his = 0.7
    test_mae_his = 8
    test_mse_his = 18

    for epoch in range(lr_steps[-1]):   # EPOCH

        if epoch in lr_steps:
            adjust_learning_rate_new(optimizer, 0.5)
        # scheduler.step(epoch)

        # Computes and stores the average and current value
        losses_cls     = AverageMeter()
        losses_cnt     = AverageMeter()
        losses_cnt2cls = AverageMeter()
        losses         = AverageMeter()

        # [rotem:] the authors override the original resnet50 train to set the training mode as desired
        cnn.train()

        for step, (batch_images, batch_classes, batch_lesions) in enumerate(train_loader):   # gives batch data, normalize x when iterate train_loader

            continue

            batch_images  = batch_images.cpu()
            batch_lesions = batch_lesions.numpy()

            # Generate lesion count distribution per batch sample
            lesion_count = gen_label_distribution(batch_lesions - 1, sigma, 'klloss', 65)

            # Split each distribution according to severity levels (see fig. 1-d in the paper)
            count_by_severity = np.vstack((np.sum(lesion_count[:, :5], 1),                      # 1-4: mild
                                           np.sum(lesion_count[:, 5:20], 1),                    # 5-19: moderate
                                           np.sum(lesion_count[:, 20:50], 1),                   # 20-49: severe
                                           np.sum(lesion_count[:, 50:], 1))).transpose()        # 50-65: very severe

            lesion_count = torch.from_numpy(lesion_count).cpu().float()
            count_by_severity = torch.from_numpy(count_by_severity).cpu().float()

            # overrides model's 'train' method to set the training mode as desired
            cnn.train()     # TODO: can this be taken outside the loop?

            # feed forward
            cls, cnt, cnt2cls = cnn(batch_images, None)

            loss_cls     = kl_loss_1(torch.log(cls), count_by_severity) * 4.0
            loss_cnt     = kl_loss_2(torch.log(cnt), lesion_count) * 65.0
            loss_cnt2cls = kl_loss_3(torch.log(cnt2cls), count_by_severity) * 4.0

            loss = (loss_cls + loss_cnt2cls) * 0.5 * lamda + loss_cnt * (1.0 - lamda)

            optimizer.zero_grad()           # clear gradients for this training step
            loss.backward()                 # backpropagation, compute gradients
            optimizer.step()                # apply gradients

            # update tracking variables
            losses_cls.update(loss_cls.item(), batch_images.size(0))
            losses_cnt.update(loss_cnt.item(), batch_images.size(0))
            losses_cnt2cls.update(loss_cnt2cls.item(), batch_images.size(0))
            losses.update(loss.item(), batch_images.size(0))


        elapsed = time_to_str((timer() - start))
        message = f'epoch {epoch} | {losses_cls.avg:.3f} | {losses_cnt.avg:.3f} | {losses_cnt2cls.avg:.3f} | {losses.avg:.3f} | {elapsed} \n'
        log.write(message)

        # Evaluate after each epoch starting from epoch number 9
        if epoch >= 0:

            with torch.no_grad():
                test_loss     = 0
                severity_hits = 0

                y_true   = np.array([])
                y_pred   = np.array([])
                y_pred_m = np.array([])
                l_true   = np.array([])
                l_pred   = np.array([])

                # Sets the model in evaluation mode
                cnn.eval()

                for step, (test_images, test_classes, test_lesions) in enumerate(test_loader):   # gives batch data, normalize x when iterate train_loader

                    test_images  = test_images.cpu()
                    test_classes = test_classes.cpu()

                    y_true = np.hstack((y_true, test_classes.data.cpu().numpy()))
                    l_true = np.hstack((l_true, test_lesions.data.cpu().numpy()))

                    cnn.eval() # TODO: is this required in each iteration?

                    cls, cnt, cnt2cls = cnn(test_images, None)

                    loss = loss_func(cnt2cls, test_classes)
                    test_loss += loss.data

                    _, preds   = torch.max(cls, 1)              # predicted severity distributions
                    _, preds_m = torch.max(cls + cnt2cls, 1)    # predicted severity distributions, plus "severity-from-count" distributions

                    y_pred   = np.hstack((y_pred, preds.data.cpu().numpy()))
                    y_pred_m = np.hstack((y_pred_m, preds_m.data.cpu().numpy()))

                    _, preds_l = torch.max(cnt, 1)
                    preds_l = (preds_l + 1).data.cpu().numpy()
                    l_pred = np.hstack((l_pred, preds_l))

                    severity_hits += torch.sum((preds == test_classes)).data.cpu().numpy()

                test_loss = test_loss.float() / len(test_loader)
                test_acc = severity_hits / len(test_loader.dataset)
                message = '%s %6.1f | %0.3f | %0.3f\n' % ("test ", epoch, test_loss.data, test_acc)

                _, _, report = report_metrics(y_pred, y_true)
                log.write(str(report) + '\n')

                _, _, report_m = report_metrics(y_pred_m, y_true)
                log.write(str(report_m) + '\n')

                _, _, _, mae_mse_report = report_mae_mse(l_true, l_pred, y_true)
                log.write(str(mae_mse_report) + '\n')

    return cnn


cross_val_lists = ['0', '1', '2', '3', '4']
for cross_val_index in cross_val_lists:
    log.write(f'cross_val_index: {cross_val_index}\n')
    cnn = trainval_test(cross_val_index, sigma=30 * 0.1, lamda=6 * 0.1)
    np.savez(f'/host_root/home/rotem/Private/Academic/LDL-rotem/trained_model.npz', cnn=cnn)

