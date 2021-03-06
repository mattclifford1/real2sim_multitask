'''
Script to train UNet with supervised normal signal

Author: Matt Clifford
Email: matt.clifford@bristol.ac.uk
'''
import os
from argparse import ArgumentParser
import sys; sys.path.append('..'); sys.path.append('.')
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
import torch.nn.functional as F
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms
from torch.optim.lr_scheduler import ExponentialLR, StepLR

from tqdm import tqdm
import multiprocessing
from torchmetrics.functional import structural_similarity_index_measure as SSIM

from trainers.data_loader import image_handler as image_loader
from trainers.utils import train_saver, MyDataParallel
from gan_models.models_128 import GeneratorUNet, weights_init_normal, weights_init_pretrained


class trainer():
    def __init__(self, dataset_train,
                       dataset_val,
                       model,
                       save_dir,
                       batch_size=64,
                       lr=1e-4,
                       lr_decay=0.1,
                       epochs=100,
                       shuffle_train=True,
                       shuffle_val=False):
        self.dataset_train = dataset_train
        self.dataset_val = dataset_val
        self.shuffle_train = shuffle_train
        self.shuffle_val = shuffle_val
        self.model = model
        self.save_dir = save_dir
        self.batch_size = batch_size
        self.lr = lr
        self.lr_decay = lr_decay
        self.epochs = epochs
        # misc inits
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.cores = multiprocessing.cpu_count()
        print('Using ', self.cores, ' CPU cores')
        # get data loader
        self.get_data_loader(prefetch_factor=2)

    def get_data_loader(self, prefetch_factor=1):
        cores = int(self.cores/2)
        self.torch_dataloader_train = DataLoader(self.dataset_train,
                                     batch_size=self.batch_size,
                                     shuffle=self.shuffle_train,
                                     num_workers=cores,
                                     prefetch_factor=prefetch_factor)

        self.torch_dataloader_val = DataLoader(self.dataset_val,
                                     batch_size=max(1, int(self.batch_size/4)),
                                     shuffle=self.shuffle_val,
                                     num_workers=cores,
                                     prefetch_factor=prefetch_factor)

    def setup(self):
        # optimser
        self.optimiser = optim.Adam(self.model.parameters(), self.lr)
        self.scheduler = StepLR(self.optimiser, step_size=1, gamma=self.lr_decay)
        # self.scheduler = ExponentialLR(self.optimiser, gamma=self.lr_decay)
        # loss criterion for training signal
        self.loss = nn.MSELoss()
        # set up model for training
        self.model = self.model.to(self.device)
        self.model.train()

        self.running_loss = [0]

    def start(self, save_model_every=10, val_every=1):
        self.setup()
        # set up save logger for training graphs etc
        self.saver = train_saver(self.save_dir, self.model, self.lr, self.lr_decay, self.batch_size, self.dataset_train.task)
        self.val_every = val_every
        self.save_model_every = save_model_every
        step_lr = [0.95, 0.98, 0.99, 0.999, 1]
        step_epochs = [50, 100, self.epochs]
        step_num = 0
        # self.eval(epoch=0)
        for epoch in tqdm(range(self.epochs), desc="Epochs"):
            self.running_loss = []
            for step, sample in enumerate(tqdm(self.torch_dataloader_train, desc="Train Steps", leave=False)):
                self.train_step(sample)
            if epoch%self.val_every == 0:
                self.val_all(epoch)
            if epoch%self.save_model_every == 0:
                    # save the trained model
                    self.saver.save_model(self.model, epoch+1)
            # lower optimiser learning rate
            if self.ssim > step_lr[step_num]:  # if we are scoring well
                self.scheduler.step()
                step_num += 1
                print('Learning rate: ', self.scheduler.get_lr())
            # lower if training for many epochs and no improvement on SSIM
            if epoch > step_epochs[min(len(step_epochs)-1, step_num)]:
                self.scheduler.step()
                print('Learning rate: ', self.scheduler.get_lr())
            if self.ssim > 0.9999:
                break

        # training finished
        self.saver.save_model(self.model, epoch+1)

    def train_step(self, sample):
        # get training batch sample
        im_real = sample['real'].to(device=self.device, dtype=torch.float)
        im_sim = sample['sim'].to(device=self.device, dtype=torch.float)
        # zero the parameter gradients
        self.optimiser.zero_grad()
        # forward
        pred_sim = self.model(im_real)
        # loss
        loss = self.loss(pred_sim, im_sim)
        # backward pass
        loss.backward()
        self.optimiser.step()
        self.running_loss.append(loss.cpu().detach().numpy()) # save the loss stats

    def val_all(self, epoch):
        self.model.eval()
        MSEs = []
        SSIMs = []
        ims_to_save = 3
        ims = []
        for step, sample in enumerate(tqdm(self.torch_dataloader_val, desc="Val Steps", leave=False)):
            # get val batch sample
            im_real = sample['real'].to(device=self.device, dtype=torch.float)
            im_sim = sample['sim'].to(device=self.device, dtype=torch.float)
            # forward
            pred_sim = self.model(im_real)
            # get metrics
            mse = torch.square(pred_sim - im_sim).mean()
            MSEs.append(mse.cpu().detach().numpy())
            ssim = SSIM(pred_sim, im_sim)
            SSIMs.append(ssim.cpu().detach().numpy())

            # store some ims to save to inspection
            if len(ims) < ims_to_save:
                ims.append({'predicted': pred_sim[0,0,:,:],
                            'simulated': im_sim[0,0,:,:],
                            'real': im_real[0,0,:,:]})

        self.model.train()
        self.MSE = sum(MSEs) / len(MSEs)
        self.ssim = sum(SSIMs) / len(SSIMs)
        stats = {'epoch': [epoch],
                 'mean training loss': [np.mean(self.running_loss)],
                 'val MSE': [self.MSE],
                 'val_SSIM': [self.ssim]}

        self.saver.log_training_stats(stats)
        self.saver.log_val_images(ims, epoch)
        # now save to csv/plot graphs



if __name__ == '__main__':
    parser = ArgumentParser(description='Test data with GAN models')
    parser.add_argument("--dir", default='..', help='path to folder where data and models are held')
    parser.add_argument("--epochs", type=int, default=250, help='number of epochs to train for')
    parser.add_argument("--task", type=str, nargs='+', default=['edge_2d', 'tap'], help='dataset to train on')
    parser.add_argument("--batch_size",type=int,  default=64, help='batch size to load and train on')
    parser.add_argument("--lr",type=float,  default=0.001, help='learning rate for optimiser')
    parser.add_argument("--pretrained_model", default=False, help='path to model to load pretrained weights on')
    parser.add_argument("--pretrained_name", default='test', help='name to refer to the pretrained model')
    parser.add_argument("--multi_GPU", default=False, action='store_true', help='run on multiple gpus if available')
    ARGS = parser.parse_args()

    print('training on: ', ARGS.task)
    dataset_train = image_loader(base_dir=ARGS.dir, task=ARGS.task)
    dataset_val = image_loader(base_dir=ARGS.dir, val=True, task=ARGS.task)
    generator = GeneratorUNet(in_channels=1, out_channels=1)
    if torch.cuda.device_count() > 1 and ARGS.multi_GPU:
        print("Using ", torch.cuda.device_count(), "GPUs")
        generator = MyDataParallel(generator)

    if ARGS.pretrained_model == False:
        generator.apply(weights_init_normal)
        # generator.name = 'test'
    else:
        weights_init_pretrained(generator, ARGS.pretrained_model, name=ARGS.pretrained_name)

    train = trainer(dataset_train,
                    dataset_val,
                    generator,
                    save_dir=os.path.join(ARGS.dir, 'models', 'sim2real', 'matt'),
                    batch_size=ARGS.batch_size,
                    epochs=ARGS.epochs,
                    lr=ARGS.lr)
    train.start()
