'''
Script to test UNet against the validation set and a given discriminator.
Code adapted from Alex Churh's tactile_gym_sim2real repo

Author: Matt Clifford
Email: matt.clifford@bristol.ac.uk
'''
import torch
import os
from skimage import io
import matplotlib.pyplot as plt
from tqdm import tqdm

from gan_net import GAN_io
from image_transforms import *
from tactile_gym_sim2real.pix2pix.gan_models.models_128 import GeneratorUNet, Discriminator


def show_ims(ims):
    fig, axs = plt.subplots(len(ims))
    for i in range(len(ims)):
        axs[i].imshow(ims[i])
    plt.show()

def model_to_device(model):
    cuda = True if torch.cuda.is_available() else False
    if cuda:
        model = model.cuda()
    else:
        model = model
    return model

class gan_tester():
    def __init__(self, gan_model_dir, image_size=[128, 128]):
        self.gan_model_dir = gan_model_dir
        self.image_size = image_size
        self.init_generator()
        self.init_discrim()

    def init_generator(self):
        self.generator = GeneratorUNet(in_channels=1, out_channels=1)
        self.generator.load_state_dict(torch.load(os.path.join(self.gan_model_dir, 'checkpoints/final_generator.pth')))
        self.generator = model_to_device(self.generator)
        self.generator.eval()
        # get image io helper
        self.gen_ims_io = GAN_io(self.gan_model_dir, rl_image_size=self.image_size)

    def init_discrim(self):
        self.discriminator = Discriminator(in_channels=1)
        self.discriminator.load_state_dict(torch.load(os.path.join(self.gan_model_dir, 'checkpoints/final_discriminator.pth')))
        self.discriminator = model_to_device(self.discriminator)
        self.discriminator.eval()

    def load_ims(self, real_image_file, sim_image_file):
        image_real = io.imread(real_image_file)
        image_sim = io.imread(sim_image_file)
        # preprocess image
        im_preprocced = self.gen_ims_io.process_raw_image(image_real)
        # convert to tensors
        im_real_pt = self.gen_ims_io.to_tensor(im_preprocced)
        im_sim_pt = self.gen_ims_io.to_tensor(image_sim)
        return im_real_pt, im_sim_pt, image_sim

    def get_info(self, image_real, image_sim):
        im_real_pt, im_sim_pt, image_sim = self.load_ims(image_real, image_sim)
        MSE, pred_sim = get_mse(self.generator, im_real_pt, image_sim, self.gen_ims_io)
        # print('MSE: ', MSE)
        # discriminator is conditions the generated/simulated image with the real camera image
        img_input = torch.cat((pred_sim, im_real_pt), 1)
        discrim_out = self.discriminator(pred_sim, im_real_pt)
        # print(discrim_out)
        discrim_avg_score = discrim_out.detach().cpu().numpy().mean()
        # print('Discriminator mean of all patches: ', discrim_avg_score)
        return MSE, discrim_avg_score


    def loop_all_params(self):
        for name, param in self.generator.named_parameters():
            print(name, ': ', param.shape)


def get_mse(generator, im_real_pt, image_sim, ims_io):
    # get pred_sim
    pred_sim = generator(im_real_pt)
    generated_sim_image = ims_io.to_numpy(pred_sim)

    # show_ims([image, processed_real_image, generated_sim_image, image_sim])

    MSE = (generated_sim_image - image_sim).mean()
    return MSE, pred_sim

def get_all_test_ims(dir, ext='.png'):
    ims = []
    for im in os.listdir(dir):
        if os.path.splitext(im)[1] == ext:
            ims.append(im)
    return ims


if __name__ == '__main__':
    '''
    input:
    - test dataset
    - generator
    - discriminator
    output:
    - test score (MSE)
    - discriminator score (error etc.)
    '''
    gan_model_dir = 'no_git_data/128x128_tap_250epochs/'
    gan_model_dir = '../models/sim2real/alex/trained_gans/[edge_2d]/128x128_[tap]_250epochs'
    # gan_model_dir = '../models/sim2real/alex/trained_gans/[edge_2d]/128x128_[shear]_250epochs'
    # gan_model_dir = '../models/sim2real/alex/trained_gans/[surface_3d]/128x128_[shear]_250epochs'
    tester = gan_tester(gan_model_dir)
    # tester.loop_all_params()

    real_images_dir = '../data/Bourne/tactip/real/edge_2d/tap/csv_val/images'
    sim_images_dir = '../data/Bourne/tactip/sim/edge_2d/tap/128x128/csv_val/images'

    MSEs = []
    discrim_scores = []
    for image in tqdm(get_all_test_ims(real_images_dir)):
        # get image pair
        test_image_real = os.path.join(real_images_dir, image)
        test_image_sim = os.path.join(sim_images_dir, image)
        # now test image pair
        MSE, discrim_avg_score = tester.get_info(test_image_real, test_image_sim)
        MSEs.append(MSE)
        discrim_scores.append(discrim_avg_score)
    print(np.mean(MSEs))
    print(np.mean(discrim_scores))