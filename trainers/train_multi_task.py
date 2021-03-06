'''
Script to train UNet with adverserial training.
Code adapted from Alex Churh's tactile_gym_sim2real repo

Author: Matt Clifford
Email: matt.clifford@bristol.ac.uk
'''
import os
from argparse import ArgumentParser
import sys; sys.path.append('..'); sys.path.append('.')
import numpy as np
import itertools
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
from gan_models.models_128 import GeneratorUNet, Discriminator, weights_init_normal, weights_init_pretrained


def concat_samples(samples):
    '''turn batch of samples from each task into one tensor to train on'''
    sample = samples[0]
    for i in range(1, len(samples)):
        for key in sample.keys():
            sample[key] = torch.cat((sample[key], samples[i][key]), axis=0)
    return sample


class trainer():
    def __init__(self, datasets_train,
                       datasets_val,
                       model,
                       discrim=None,
                       save_dir='models',
                       batch_size=64,
                       lr=1e-4,
                       lr_decay=0.1,
                       epochs=100,
                       shuffle_train=True,
                       shuffle_val=False):
        self.datasets_train = datasets_train
        self.datasets_val = datasets_val
        self.shuffle_train = shuffle_train
        self.shuffle_val = shuffle_val
        self.model = model
        self.discriminator = discrim
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
        self.get_data_loaders(prefetch_factor=1)

    def get_data_loaders(self, prefetch_factor=1):
        cores = int(self.cores/2)
        scaled_batch_size = int(self.batch_size/len(self.datasets_train.keys()))
        # scaled_batch_size_val = int(self.batch_size/len(self.datasets_val.keys()))
        scaled_batch_size_val = self.batch_size
        self.torch_dataloaders_train = {}
        self.torch_dataloaders_val = {}
        for task in self.datasets_train.keys():
            self.torch_dataloaders_train[task] = DataLoader(self.datasets_train[task],
                                         batch_size=max(1, scaled_batch_size),
                                         shuffle=self.shuffle_train,
                                         num_workers=cores,
                                         prefetch_factor=prefetch_factor)
        for task in self.datasets_val.keys():
            self.torch_dataloaders_val[task] = DataLoader(self.datasets_val[task],
                                         batch_size=max(1, int(scaled_batch_size_val/4)),
                                         shuffle=self.shuffle_val,
                                         num_workers=cores,
                                         prefetch_factor=prefetch_factor)

    def setup(self):
        # optimser
        self.optimiser = optim.Adam(self.model.parameters(), self.lr)
        self.scheduler = StepLR(self.optimiser, step_size=1, gamma=self.lr_decay)
        if self.discriminator is not None:
            # optimser for the discriminator
            self.optimiser_discrim = optim.Adam(self.discriminator.parameters(), self.lr)
            self.scheduler_discrim = StepLR(self.optimiser_discrim, step_size=1, gamma=self.lr_decay)
        # self.scheduler = ExponentialLR(self.optimiser, gamma=self.lr_decay)
        # loss criterion for training signal
        self.loss = nn.MSELoss()
        # set up model for training
        self.model = self.model.to(self.device)
        self.model.train()
        if self.discriminator is not None:
            self.discriminator = self.discriminator.to(self.device)
            self.discriminator.train()
            # gan training signals
            self.loss_gan = nn.MSELoss()
            self.loss_pixel_wise  = nn.L1Loss()

        self.running_loss = [0]
        self.ssim = 0
        # attributes to step the learning rate scheduler
        self.step_lr = [0.95, 0.98, 0.99, 0.999, 1]
        self.step_epochs = [50, 100, self.epochs]
        self.step_num = 0

    def get_saver(self):
        # set up save logger for training graphs etc
        if self.discriminator is not None:
            name = 'GAN_'
        else:
            name = 'no_gan'

        self.saver = train_saver(self.save_dir,
                                 self.model,
                                 self.lr,
                                 self.lr_decay,
                                 self.batch_size,
                                 ('both', 'both'),
                                 name)

    def start(self, val_every=1, save_model_every=100):
        self.setup()
        self.get_saver()
        self.val_every = val_every
        self.save_model_every = save_model_every
        # self.val_all(epoch=0)
        for epoch in tqdm(range(self.epochs), desc="Epochs"):
            self.epoch = epoch
            self.running_loss = []
            for step, samples in enumerate(tqdm(zip(*self.torch_dataloaders_train.values()), desc="Train Steps", leave=False)):
                sample = concat_samples(samples)
                self.train_step(sample)
            if self.epoch%self.val_every == 0:
                self.val_all(self.epoch+1)
                self.maybe_save_model()
            self.check_to_lower_learning_rate()
            if self.ssim > 0.9999:
                break

        # # training finished
        # self.saver.save_model(self.model, 'final_generator')

    def train_step(self, sample):
        # get training batch sample
        im_real = sample['real'].to(device=self.device, dtype=torch.float)
        im_sim = sample['sim'].to(device=self.device, dtype=torch.float)
        if self.discriminator is not None:
            gan_gt_real = torch.ones(im_real.size(0), 1, self.discriminator.out_size, self.discriminator.out_size).to(device=self.device, dtype=torch.float)
            gan_gt_fake = torch.zeros(im_real.size(0), 1, self.discriminator.out_size, self.discriminator.out_size).to(device=self.device, dtype=torch.float)
        # zero the parameter gradients
        self.optimiser.zero_grad()
        # forward
        pred_sim = self.model(im_real)
        # loss
        if self.discriminator is not None:
            pred_fake = self.discriminator(pred_sim, im_real)
            loss_gan = self.loss_gan(pred_fake, gan_gt_real)
            loss_pixel = self.loss_pixel_wise(pred_sim, im_sim)
            loss = 0.01*loss_gan + loss_pixel
        else:
            loss = self.loss(pred_sim, im_sim)
        # backward pass
        loss.backward()
        self.optimiser.step()
        self.running_loss.append(loss.cpu().detach().numpy()) # save the loss stats

        # train discriminator
        if self.discriminator is not None:
            self.optimiser_discrim.zero_grad()
            pred_real = self.discriminator(im_sim, im_real)
            loss_real = self.loss_gan(pred_real, gan_gt_real)
            pred_fake = self.discriminator(pred_sim.detach(), im_real)
            loss_fake = self.loss_gan(pred_fake, gan_gt_fake)

            loss = 0.5 * (loss_real + loss_fake)
            loss.backward()
            self.optimiser_discrim.step()

    def val_all(self, epoch):
        '''
        TODO:
        make this save val stats properly!!!
        '''
        self._last_ssim = self.ssim
        self.model.eval()
        MSEs = []
        SSIMs = []
        ims_to_save = 3
        ims = []
        for key in tqdm(self.torch_dataloaders_val.keys(), desc='Val Dataset', leave=False):
            for step, sample in enumerate(tqdm(self.torch_dataloaders_val[key], desc="Val Steps", leave=False)):
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

    def maybe_save_model(self):
        if self._last_ssim < self.ssim:
            self.saver.save_model(self.model, 'best_generator')
        # legacy code to save every x epochs
        # if epoch%self.save_model_every == 0:
        #         # save the trained model
        #         self.saver.save_model(self.model, epoch+1)
        # lower optimiser learning rate

    def check_to_lower_learning_rate(self):
        if self.ssim > self.step_lr[self.step_num]:  # if we are scoring well
            self.scheduler.step()
            self.step_num += 1
            print('Learning rate: ', self.scheduler.get_lr())
        # lower if training for many epochs and no improvement on SSIM
        if self.epoch > self.step_epochs[min(len(self.step_epochs)-1, self.step_num)]:
            self.scheduler.step()
            print('Learning rate: ', self.scheduler.get_lr())

def get_all_loaders(dir, store_ram=False):
    # get all combinations of models and data
    task = ['edge_2d','surface_3d']
    sampling = ['tap', 'shear']
    tasks = list(itertools.product(task, sampling))
    print('training on: ', tasks)
    datasets_train = {}
    datasets_val = {}
    for task in tasks:
        datasets_train[task[0]+' '+task[1]] = image_loader(base_dir=dir, task=task, store_ram=store_ram)
        datasets_val[task[0]+' '+task[1]] = image_loader(base_dir=dir, val=True, task=task, store_ram=store_ram)
    return datasets_train, datasets_val


if __name__ == '__main__':
    parser = ArgumentParser(description='Test data with GAN models')
    parser.add_argument("--dir", default='..', help='path to folder where data and models are held')
    parser.add_argument("--epochs", type=int, default=250, help='number of epochs to train for')
    parser.add_argument("--batch_size",type=int,  default=64, help='batch size to load and train on')
    parser.add_argument("--lr",type=float,  default=0.001, help='learning rate for optimiser')
    parser.add_argument("--pretrained_model", default=False, help='path to model to load pretrained weights on')
    parser.add_argument("--pretrained_name", default='test', help='name to refer to the pretrained model')
    parser.add_argument("--multi_GPU", default=False, action='store_true', help='run on multiple gpus if available')
    parser.add_argument("--GAN", default=False, action='store_true', help='train against discriminator')
    parser.add_argument("--ram", default=False, action='store_true', help='load dataset into ram')
    ARGS = parser.parse_args()

    datasets_train, datasets_val = get_all_loaders(ARGS.dir, ARGS.ram)

    generator = GeneratorUNet(in_channels=1, out_channels=1)
    if ARGS.GAN == True:
        discriminator = Discriminator(in_channels=1)
    else:
        discriminator = None

    if torch.cuda.device_count() > 1 and ARGS.multi_GPU:
        print("Using ", torch.cuda.device_count(), "GPUs")
        generator = MyDataParallel(generator)
        if ARGS.GAN == True:
            discriminator = MyDataParallel(discriminator)

    if ARGS.pretrained_model == False:
        generator.apply(weights_init_normal)
        if ARGS.GAN == True:
            discriminator.apply(weights_init_normal)
    else:
        weights_init_pretrained(generator, ARGS.pretrained_model, name=ARGS.pretrained_name)

    train = trainer(datasets_train,
                    datasets_val,
                    generator,
                    discriminator,
                    save_dir=os.path.join(ARGS.dir, 'models', 'sim2real', 'matt'),
                    batch_size=ARGS.batch_size,
                    epochs=ARGS.epochs,
                    lr=ARGS.lr)
    train.start()
