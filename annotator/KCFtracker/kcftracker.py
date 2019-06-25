import numpy as np 
import cv2
from time import time
from datetime import datetime

from .fhog import *
from .yamlConfigHandling import load_config

# ffttools
def fftd(img, backwards=False):	
	# shape of img can be (m,n), (m,n,1) or (m,n,2)	
	# in my test, fft provided by numpy and scipy are slower than cv2.dft
	return cv2.dft(np.float32(img), flags = ((cv2.DFT_INVERSE | cv2.DFT_SCALE) if backwards else cv2.DFT_COMPLEX_OUTPUT))   # 'flags =' is necessary!
	
def real(img):
	return img[:,:,0]
	
def imag(img):
	return img[:,:,1]
		
def complexMultiplication(a, b):
	res = np.zeros(a.shape, a.dtype)
	res[:, :, 0] = a[:, :, 0]*b[:, :, 0] - a[:, :, 1]*b[:, :, 1]
	res[:, :, 1] = a[:, :, 0]*b[:, :, 1] + a[:, :, 1]*b[:, :, 0]
	return res

def complexDivision(a, b):
	res = np.zeros(a.shape, a.dtype)
	divisor = 1. / (b[:, :, 0]**2 + b[:, :, 1]**2)
	
	res[:,:,0] = (a[:, :, 0]*b[:, :, 0] + a[:, :, 1]*b[:, :, 1]) * divisor
	res[:,:,1] = (a[:, :, 1]*b[:, :, 0] + a[:, :, 0]*b[:, :, 1]) * divisor
	return res

# def rearrange(img):
# 	assert(img.ndim==2)
# 	img_ = np.zeros(img.shape, img.dtype)
# 	xh, yh = int(img.shape[1]/2), int(img.shape[0]/2)
# 	if(img.shape[1]%2 != 0):
# 		if(img.shape[1]/2 < xh + 0.5):
# 			i = 1
# 			k = 0
# 		else:
# 			i = 0
# 			k = 1
# 	else:
# 		i = 0
# 		k = 0
# 	if(img.shape[0]%2 != 0):
# 		if(img.shape[0]/2 < yh + 0.5):
# 			j = 1
# 			l = 0
# 		else:
# 			j = 0
# 			l = 1
# 	else:
# 		j = 0
# 		l = 0
#
# 	img_[l:yh+l, k:xh+k], img_[yh+l:img.shape[0]-j, xh+k:img.shape[1]-i] = img[yh+l:img.shape[0]-j, xh+k:img.shape[1]-i], img[l:yh+l, k:xh+k]
# 	img_[l:yh+l, xh+k:img.shape[1]-i], img_[yh+l:img.shape[0]-j, k:xh+k] = img[yh+l:img.shape[0]-j, k:xh+k], img[l:yh+l, xh+k:img.shape[1]-i]
#
# 	return img_


# recttools
def x2(rect):
	return rect[0] + rect[2]

def y2(rect):
	return rect[1] + rect[3]

def limit(rect, limit):
	if(rect[0]+rect[2] > limit[0]+limit[2]):
		rect[2] = limit[0]+limit[2]-rect[0]
	if(rect[1]+rect[3] > limit[1]+limit[3]):
		rect[3] = limit[1]+limit[3]-rect[1]
	if(rect[0] < limit[0]):
		rect[2] -= (limit[0]-rect[0])
		rect[0] = limit[0]
	if(rect[1] < limit[1]):
		rect[3] -= (limit[1]-rect[1])
		rect[1] = limit[1]
	if(rect[2] < 0):
		rect[2] = 0
	if(rect[3] < 0):
		rect[3] = 0
	return rect

def getBorder(original, limited):
	res = [0,0,0,0]
	res[0] = limited[0] - original[0]
	res[1] = limited[1] - original[1]
	res[2] = x2(original) - x2(limited)
	res[3] = y2(original) - y2(limited)
	assert(np.all(np.array(res) >= 0))
	return res

def subwindow(img, window, borderType=cv2.BORDER_CONSTANT):
	cutWindow = [x for x in window]
	limit(cutWindow, [0,0,img.shape[1],img.shape[0]])   # modify cutWindow
	assert(cutWindow[2]>0 and cutWindow[3]>0)
	border = getBorder(window, cutWindow)
	res = img[cutWindow[1]:cutWindow[1]+cutWindow[3], cutWindow[0]:cutWindow[0]+cutWindow[2]]

	if(border != [0,0,0,0]):
		res = cv2.copyMakeBorder(res, border[1], border[3], border[0], border[2], borderType)
	return res



# KCF tracker
class KCFTracker:
	def __init__(self, hog=False, fixed_window=False, multiscale=False):
		config = load_config('annotator/KCFtracker/KCF_config.yml')
		self.lambdar = config['lambdar']  # regularization
		self.padding = config['padding']  # extra area surrounding the target
		self.output_sigma_factor = config['output_sigma_factor']  # bandwidth of gaussian target
		if (hog):  # HOG feature
			# VOT
			self.interp_factor = config['interp_factor_hog']  # linear interpolation factor for adaptation
			self.sigma = config['sigma_hog']  # gaussian kernel bandwidth
			# TPAMI   #interp_factor = 0.02   #sigma = 0.5
			self.cell_size = config['cell_size_hog']  # HOG cell size
			self._hogfeatures = True
		else:  # raw gray-scale image # aka CSK tracker
			self.interp_factor = config['interp_factor_grey_scale']
			self.sigma = config['sigma_grey_scale']
			self.cell_size = config['cell_size_grey_scale']
			self._hogfeatures = False

		if (multiscale):
			self.template_size = config['template_size_multiscale']  # template size
			self.scale_step = config['scale_step_multiscale']   # scale step for multi-scale estimation
			self.scale_weight = config['scale_weight_multiscale']  # to downweight detection scores of other scales for added stability
		elif (fixed_window):
			self.template_size = config['template_size_fixed_window']
			self.scale_step = config['scale_step_fixed_window']
		else:
			self.template_size = config['template_size_else']
			self.scale_step = config['scale_step_else']

		self._template_size = [0, 0]  # cv::Size, [width,height]  #[int,int]
		self._roi = [0., 0., 0., 0.]# cv::Rect2f, [x,y,width,height]  #[float,float,float,float]
		self.roi = [0., 0., 0., 0.]
		self.size_patch = [0, 0, 0]  #[int,int,int]
		self._scale = 1.   # float
		self._alphaf = None  # numpy.ndarray    (size_patch[0], size_patch[1], 2)
		self._prob = None  # numpy.ndarray    (size_patch[0], size_patch[1], 2)
		self._template = None  # numpy.ndarray    raw: (size_patch[0], size_patch[1])   hog: (size_patch[2], size_patch[0]*size_patch[1])
		self.hann = None  # numpy.ndarray    raw: (size_patch[0], size_patch[1])   hog: (size_patch[2], size_patch[0]*size_patch[1])

		self.tt = 0.04

	def subPixelPeak(self, left, center, right):
		divisor = 2*center - right - left   #float
		return (0 if abs(divisor)<1e-3 else 0.5*(right-left)/divisor)

	def createHanningMats(self):
		hann2t, hann1t = np.ogrid[0:int(self.size_patch[0]), 0:int(self.size_patch[1])]
		hann1t = 0.5 * (1 - np.cos(2*np.pi*hann1t/(self.size_patch[1]-1)))
		hann2t = 0.5 * (1 - np.cos(2*np.pi*hann2t/(self.size_patch[0]-1)))
		hann2d = hann2t * hann1t

		if(self._hogfeatures):
			hann1d = hann2d.reshape(int(self.size_patch[0]*self.size_patch[1]))
			self.hann = np.zeros((int(self.size_patch[2]), 1), np.float32) + hann1d
		else:
			self.hann = hann2d
		self.hann = self.hann.astype(np.float32)

	def createGaussianPeak(self, sizey, sizex):
		syh, sxh = sizey/2, sizex/2
		output_sigma = np.sqrt(sizex*sizey) / self.padding * self.output_sigma_factor
		mult = -0.5 / (output_sigma*output_sigma)
		y, x = np.ogrid[0:sizey, 0:sizex]
		y, x = (y-syh)**2, (x-sxh)**2
		res = np.exp(mult * (y+x))
		return fftd(res)

	def gaussianCorrelation(self, x1, x2):
		###The gaussian kernel function
		if(self._hogfeatures):
			c = np.zeros((int(self.size_patch[0]), int(self.size_patch[1]), 2), np.float32)
			for i in range(int(self.size_patch[2])):
				x1aux = x1[i, :].reshape((int(self.size_patch[0]), int(self.size_patch[1])))
				x2aux = x2[i, :].reshape((int(self.size_patch[0]), int(self.size_patch[1])))
				caux = cv2.mulSpectrums(fftd(x1aux), fftd(x2aux), 0, conjB = True)
				c += caux
			c = real(cv2.idft(c, flags=(cv2.DFT_SCALE)))
		else:
			c = cv2.mulSpectrums(fftd(x1), fftd(x2), 0, conjB = True)   # 'conjB=' is necessary!
			c = real(cv2.idft(c, flags=(cv2.DFT_SCALE)))

		if(x1.ndim==3 and x2.ndim==3):
			d = (np.sum(x1[:, :, 0] * x1[:, :, 0]) + np.sum(x2[:, :, 0] * x2[:, :, 0]) - 2.0 * c) / (
						self.size_patch[0] * self.size_patch[1] * self.size_patch[2])
		elif(x1.ndim==2 and x2.ndim==2):
			d = (np.sum(x1**2) + np.sum(x2**2) - 2.0*c) / (self.size_patch[0]*self.size_patch[1]*self.size_patch[2])

		d = d * (d>=0)
		d = np.exp(-d / (self.sigma*self.sigma))

		return d

	def getFeatures(self, image, inithann, scale_adjust=1.0):
		extracted_roi = [0, 0, 0, 0]   #[int,int,int,int]
		cx = self._roi[0] + self._roi[2]/2  #float
		cy = self._roi[1] + self._roi[3]/2  #float

		if(inithann):
			padded_width = self._roi[2] * self.padding
			padded_height = self._roi[3] * self.padding

			if(self.template_size > 1):
				if(padded_width >= padded_height):
					self._scale = padded_width / float(self.template_size)
				else:
					self._scale = padded_height / float(self.template_size)
				self._template_size[0] = int(padded_width / self._scale)
				self._template_size[1] = int(padded_height / self._scale)
			else:
				self._template_size[0] = int(padded_width)
				self._template_size[1] = int(padded_height)
				self._scale = 1.

			if(self._hogfeatures):
				self._template_size[0] = int(self._template_size[0] / (2*self.cell_size) * 2*self.cell_size + 2*self.cell_size)
				self._template_size[1] = int(self._template_size[1] / (2*self.cell_size) * 2*self.cell_size + 2*self.cell_size)
			else:
				self._template_size[0] = int(self._template_size[0] / 2 * 2)
				self._template_size[1] = int(self._template_size[1] / 2 * 2)

		extracted_roi[2] = int(scale_adjust * self._scale * self._template_size[0])
		extracted_roi[3] = int(scale_adjust * self._scale * self._template_size[1])
		extracted_roi[0] = int(cx - extracted_roi[2]/2)
		extracted_roi[1] = int(cy - extracted_roi[3]/2)

		boxImage = subwindow(image, extracted_roi, cv2.BORDER_REPLICATE)
		if(boxImage.shape[1]!=self._template_size[0] or boxImage.shape[0]!=self._template_size[1]):
			boxImage = cv2.resize(boxImage, tuple(self._template_size))
		if(self._hogfeatures):
			featureMap = {'sizeX':0, 'sizeY':0, 'numFeatures':0, 'map':0}
			featureMap = getFeatureMaps(boxImage, self.cell_size, featureMap) #Create the hog-feature map
			featureMap = normalizeAndTruncate(featureMap, 0.2) #creates normalized features and truncates the map
			featureMap = PCAFeatureMaps(featureMap) #Creates new features that should better describe it

			self.size_patch = list(map(float, [featureMap['sizeY'], featureMap['sizeX'], featureMap['numFeatures']]))
			finalFeaturesMap = featureMap['map'].reshape((int(self.size_patch[0]*self.size_patch[1]), int(self.size_patch[2]))).T   # (size_patch[2], size_patch[0]*size_patch[1])
		else:
			if(boxImage.ndim==3 and boxImage.shape[2]==3):
				finalFeaturesMap = cv2.cvtColor(boxImage, cv2.COLOR_BGR2GRAY)   # boxImage:(size_patch[0], size_patch[1], 3)  FeaturesMap:(size_patch[0], size_patch[1])   #np.int8  #0~255
			elif(boxImage.ndim==2):
				finalFeaturesMap = boxImage   #(size_patch[0], size_patch[1]) #np.int8  #0~255
			finalFeaturesMap = finalFeaturesMap.astype(np.float32) / 255.0 - 0.5
			self.size_patch = [boxImage.shape[0], boxImage.shape[1], 1]

		if(inithann):
			self.createHanningMats()  # createHanningMats need size_patch

		finalFeaturesMap = self.hann * finalFeaturesMap
		return finalFeaturesMap

	def detect(self, z, x):
		kxz = self.gaussianCorrelation(x, z)
		res = real(fftd(complexMultiplication(self._alphaf, fftd(kxz)), True))

		_, pv, _, pi = cv2.minMaxLoc(res)   # pv:float  pi:tuple of int
		p = [float(pi[0]), float(pi[1])]   # cv::Point2f, [x,y]  #[float,float]

		if(pi[0]>0 and pi[0]<res.shape[1]-1):
			p[0] += self.subPixelPeak(res[pi[1],pi[0]-1], pv, res[pi[1],pi[0]+1])
		if(pi[1]>0 and pi[1]<res.shape[0]-1):
			p[1] += self.subPixelPeak(res[pi[1]-1,pi[0]], pv, res[pi[1]+1,pi[0]])

		p[0] -= res.shape[1] / 2.
		p[1] -= res.shape[0] / 2.
		return p, pv

	def train(self, x, train_interp_factor):
		kxx = self.gaussianCorrelation(x, x)
		alphaf = complexDivision(self._prob, fftd(kxx)+self.lambdar)

		self._template = (1-train_interp_factor)*self._template + train_interp_factor*x
		self._alphaf = (1-train_interp_factor)*self._alphaf + train_interp_factor*alphaf


	def init(self, roi, image):
		self._roi = list(map(float, roi))
		assert(roi[2]>0 and roi[3]>0)
		self._template = self.getFeatures(image, 1)
		self._prob = self.createGaussianPeak(self.size_patch[0], self.size_patch[1])
		self._alphaf = np.zeros((int(self.size_patch[0]), int(self.size_patch[1]), 2), np.float32)
		self.train(self._template, 1.0)

	def update(self, image):
		if(self._roi[0]+self._roi[2] <= 0):  self._roi[0] = -self._roi[2] + 1
		if(self._roi[1]+self._roi[3] <= 0):  self._roi[1] = -self._roi[2] + 1
		if(self._roi[0] >= image.shape[1]-1):  self._roi[0] = image.shape[1] - 2
		if(self._roi[1] >= image.shape[0]-1):  self._roi[1] = image.shape[0] - 2

		cx = self._roi[0] + self._roi[2]/2.
		cy = self._roi[1] + self._roi[3]/2.
		loc, peak_value = self.detect(self._template, self.getFeatures(image, 0, 1.0))
		if(self.scale_step != 1):
			# Test at a smaller _scale
			new_loc1, new_peak_value1 = self.detect(self._template, self.getFeatures(image, 0, 1.0/self.scale_step))
			# Test at a bigger _scale
			new_loc2, new_peak_value2 = self.detect(self._template, self.getFeatures(image, 0, self.scale_step))

			if(self.scale_weight*new_peak_value1 > peak_value and new_peak_value1>new_peak_value2):
				loc = new_loc1
				peak_value = new_peak_value1
				self._scale /= self.scale_step
				self._roi[2] /= self.scale_step
				self._roi[3] /= self.scale_step
			elif(self.scale_weight*new_peak_value2 > peak_value):
				loc = new_loc2
				peak_value = new_peak_value2
				self._scale *= self.scale_step
				self._roi[2] *= self.scale_step
				self._roi[3] *= self.scale_step

		self._roi[0] = cx - self._roi[2]/2.0 + loc[0]*self.cell_size*self._scale
		self._roi[1] = cy - self._roi[3]/2.0 + loc[1]*self.cell_size*self._scale
		if(self._roi[0] >= image.shape[1]-1):  self._roi[0] = image.shape[1] - 1
		if(self._roi[1] >= image.shape[0]-1):  self._roi[1] = image.shape[0] - 1
		if(self._roi[0]+self._roi[2] <= 0):  self._roi[0] = -self._roi[2] + 2
		if(self._roi[1]+self._roi[3] <= 0):  self._roi[1] = -self._roi[3] + 2
		assert(self._roi[2]>0 and self._roi[3]>0)

		x = self.getFeatures(image, 0, 1.0)
		self.train(x, self.interp_factor)
		###It should return true whether it has kept track of the object or not,
		### however at this moment it can't know if it has lost track or not so it just returns true
		return True, self._roi