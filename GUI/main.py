import sys
import argparse
import threading
import time
import pickle
import os

import numpy as np
from PyQt5.QtWidgets import QApplication, QWidget, QMainWindow, QInputDialog, QAction, QLineEdit, QMenu, QFileDialog, QPushButton, QGridLayout, QLabel, QSlider, QComboBox, qApp
from PyQt5.QtGui import QPixmap, QImage, QColor
from PyQt5.QtCore import Qt

from PyQt5.QtGui import QPainter, QBrush
from PyQt5.QtWidgets import QStyle, QStyleOptionSlider
from PyQt5.QtCore import QRect, QPoint, Qt

import sys; sys.path.append('..'); sys.path.append('.')
from gui_utils import change_im, load_image

class make_app(QMainWindow):
    def __init__(self, app, args):
        super().__init__()
        self.args = args
        self.app = app
        self.set_window()
        self.init_images()
        self.init_widgets()
        self.init_layout()
        change_im(self.im_Qlabels['im1'], self.dummy_im, resize=(256,256))
        change_im(self.im_Qlabels['im2'], self.dummy_im, resize=(256,256))

    def set_window(self):
        '''
        set the application window to the size of the screen
        '''
        screen = self.app.primaryScreen()
        size = screen.size()
        self.width = size.width() - 50
        self.height =  size.height() - 50

    def init_images(self, screen=False):
        '''
        make blank images to place on screen before actual image is chosen
        this creates the UI to be the correct size
        '''
        # make image placeholders
        pad = 30
        pad_small = 10
        height_reduction_factor = 1.6
        self.width_ratio = 1 # 16/9
        if screen:
            self.height = int(self.height/height_reduction_factor) - pad
        else:
            self.height = int(256)
        self.width = int(self.height*self.width_ratio)

        print(self.args.image_path)
        if os.path.exists(self.args.image_path):
            print('in')
            self.dummy_im = load_image(self.args.image_path)
        else:
            self.dummy_im = np.zeros([128, 128, 1], dtype=np.uint8)
        # set up ims and click functions
        self.im_Qlabels = {'im1':QLabel(self),
                           'im2':QLabel(self),
                           # 'im_diff':QLabel(self)
                           }
        self.im_Qlabels['im1'].setAlignment(Qt.AlignLeft)
        self.im_Qlabels['im2'].setAlignment(Qt.AlignRight)
        # self.im_Qlabels['colour_output'].mousePressEvent = self.image_click
        # hold video frames
        # dummy_frame = [self.dummy_im]*2
        # self.input_frames = {'UI_colour_original':dummy_frame,
                             # 'UI_depth':dummy_frame}
        # self.num_frames = len(self.input_frames['UI_colour_original'])

    def init_widgets(self):
        '''
        create all the widgets we need and init params
        '''
        # load images
        self.button_image1 = QPushButton('Choose Image 1', self)
        self.button_image1.clicked.connect(self.choose_image1)
        self.button_image2 = QPushButton('Choose Image 2', self)
        self.button_image2.clicked.connect(self.choose_image2)

    def init_layout(self):
        '''
        place all the widgets in the window
        '''
        # make main widget insdie the QMainWindow
        self.main_widget = QWidget()
        self.layout = QGridLayout()
        self.main_widget.setLayout(self.layout)
        self.setCentralWidget(self.main_widget)
        # sizes
        im_width = 6
        im_height = 6
        button = 1
        slider_width = 3
        # horizonal start values
        start_im = 0
        start_controls = im_width*2
        start_sliders = 3

        # load files
        self.layout.addWidget(self.button_image1, 0, start_controls, button, button)
        self.layout.addWidget(self.button_image2, 1, start_controls, button, button)
        # display images
        self.layout.addWidget(self.im_Qlabels['im1'], 0, start_im, im_height, im_width)
        self.layout.addWidget(self.im_Qlabels['im2'], start_im, im_width, im_height, im_width)
        # self.layout.addWidget(self.im_Qlabels['im_diff'], im_height+1, im_width//2, im_height//2, im_width//2)
        # init it!
        self.show()

    '''
    ==================== functions to bind to widgets ====================
    '''
    def choose_image1(self):
        '''
        choose video file from Files
        '''
        try:
            self.image1_file, _ = QFileDialog.getOpenFileName(self,"QFileDialog.getOpenFileName()", "","All Files (*);;Image Files (*.png)")#, options=options)
            if self.image1_file != '':
                # self.reset_all_sliders()
                # self.get_list_of_ims(self.video_file, button=self.button_video)
                print('now impliment load image')
        except:
            self.statusBar().showMessage('Cancelled Load')

    def choose_image2(self):
        '''
        choose video file from Files
        '''
        try:
            self.image2_file, _ = QFileDialog.getOpenFileName(self,"QFileDialog.getOpenFileName()", "","All Files (*);;Image Files (*.png)")#, options=options)
            if self.image2_file != '':
                # self.reset_all_sliders()
                # self.get_list_of_ims(self.video_file, button=self.button_video)
                print('now impliment load image')
        except:
            self.statusBar().showMessage('Cancelled Load')

    '''
    image updaters
    '''


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--image_path', type=str, help='image file to use', default=os.path.join(os.path.expanduser('~'),'summer-project/data/Bourne/tactip/sim/surface_3d/tap/128x128/csv_train/images/image_1.png'))
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = make_app(app, args)
    sys.exit(app.exec_())
