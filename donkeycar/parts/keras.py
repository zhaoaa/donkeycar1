'''

pilots.py

Methods to create, use, save and load pilots. Pilots 
contain the highlevel logic used to determine the angle
and throttle of a vehicle. Pilots can include one or more 
models to help direct the vehicles motion. 

'''




import os
os.environ['KERAS_BACKEND'] = 'tensorflow'  # chongwui
import numpy as np
import keras
import time
import tensorflow as tf

import donkeycar as dk
import donkeycar.constant as Constant
from donkeycar.parts.fuzzy import fuzzy

class KerasPilot():
 
    def load(self, model_path):
        self.model = keras.models.load_model(model_path)

        # to handle multi threaded issue https://github.com/keras-team/keras/issues/2397
        self.model._make_predict_function()
        self.graph = tf.get_default_graph()

    
    def shutdown(self):
        pass
    
    
    def train(self, train_gen, val_gen, 
              saved_model_path, epochs=100, steps=100, train_split=0.8,
              verbose=1, min_delta=.0005, patience=5, use_early_stop=True):
        
        """
        train_gen: generator that yields an array of images an array of 
        
        """

        #checkpoint to save model after each epoch
        save_best = keras.callbacks.ModelCheckpoint(saved_model_path, 
                                                    monitor='val_loss', 
                                                    verbose=verbose, 
                                                    save_best_only=True, 
                                                    mode='min')
        
        #stop training if the validation error stops improving.
        early_stop = keras.callbacks.EarlyStopping(monitor='val_loss', 
                                                   min_delta=min_delta, 
                                                   patience=patience, 
                                                   verbose=verbose, 
                                                   mode='auto')
        
        callbacks_list = [save_best]

        if use_early_stop:
            callbacks_list.append(early_stop)
        
        hist = self.model.fit_generator(
                        train_gen, 
                        steps_per_epoch=steps, 
                        epochs=epochs, 
                        verbose=1, 
                        validation_data=val_gen,
                        callbacks=callbacks_list, 
                        validation_steps=steps*(1.0 - train_split))
        return hist


class KerasCategorical(KerasPilot):
    def __init__(self, model=None, *args, **kwargs):
        super(KerasCategorical, self).__init__(*args, **kwargs)
        if model:
            self.model = model
        else:
            self.model = default_categorical()
        
    def run(self, img_arr):
        img_arr = img_arr.reshape((1,) + img_arr.shape)
        angle_binned, throttle = self.model.predict(img_arr)
        #print('throttle', throttle)
        #angle_certainty = max(angle_binned[0])
        angle_unbinned = dk.utils.linear_unbin(angle_binned)
        return angle_unbinned, throttle[0][0]
    
    
    
class KerasLinear(KerasPilot):
    def __init__(self, model=None, num_outputs=None, *args, **kwargs):
        super(KerasLinear, self).__init__(*args, **kwargs)
        if model:
            self.model = model
        elif num_outputs is not None:
            self.model = default_n_linear(num_outputs)
        else:
            self.model = default_linear()
    def run(self, img_arr):
        img_arr = img_arr.reshape((1,) + img_arr.shape)
        outputs = self.model.predict(img_arr)
        #print(len(outputs), outputs)
        steering = outputs[0]
        throttle = outputs[1]
        return steering[0][0], throttle[0][0]



class KerasIMU(KerasPilot):
    '''
    A Keras part that take an image and IMU vector as input,
    outputs steering and throttle

    Note: When training, you will need to vectorize the input from the IMU.
    Depending on the names you use for imu records, something like this will work:

    X_keys = ['cam/image_array','imu_array']
    y_keys = ['user/angle', 'user/throttle']
    
    def rt(rec):
        rec['imu_array'] = np.array([ rec['imu/acl_x'], rec['imu/acl_y'], rec['imu/acl_z'],
            rec['imu/gyr_x'], rec['imu/gyr_y'], rec['imu/gyr_z'], rec['imu/temp'] ])
        return rec

    kl = KerasIMU()

    tubgroup = TubGroup(tub_names)
    train_gen, val_gen = tubgroup.get_train_val_gen(X_keys, y_keys, record_transform=rt,
                                                    batch_size=cfg.BATCH_SIZE,
                                                    train_frac=cfg.TRAIN_TEST_SPLIT)

    '''
    def __init__(self, model=None, num_outputs=2, num_imu_inputs=7 , *args, **kwargs):
        super(KerasIMU, self).__init__(*args, **kwargs)
        self.num_imu_inputs = num_imu_inputs
        self.model = default_imu(num_outputs = num_outputs, num_imu_inputs = num_imu_inputs)
        
    def run(self, img_arr, accel_x, accel_y, accel_z, gyr_x, gyr_y, gyr_z, temp):
        #TODO: would be nice to take a vector input array.
        img_arr = img_arr.reshape((1,) + img_arr.shape)
        imu_arr = np.array([accel_x, accel_y, accel_z, gyr_x, gyr_y, gyr_z, temp]).reshape(1,self.num_imu_inputs)
        outputs = self.model.predict([img_arr, imu_arr])
        steering = outputs[0]
        throttle = outputs[1]
        return steering[0][0], throttle[0][0]

class KerasFuzzyAndUltrasonicSensors(KerasPilot):
    def __init__(self, model=None, num_ultrasonic_inputs = 3, *args, **kwargs):
        super(KerasFuzzyAndUltrasonicSensors, self).__init__(*args, **kwargs)
        self.num_ultrasonic_inputs = num_ultrasonic_inputs
        self.pilot_angle = 0
        self.pilot_throttle = 0
        self.img_arr = None
        self.ultrasonic_front_distance = 0
        self.ultrasonic_front_left_distance = 0
        self.ultrasonic_front_right_distance = 0
        self.obstacle = 0

        if model:
            self.model = model
        else:
#            self.model = default_ultrasonicSensors(num_ultrasonic_inputs = num_ultrasonic_inputs)
            self.model = default_categoricalCustom()

        # set the inference engine
        self.fuzzy = fuzzy()
        self.on = True
		
    def predict(self):
        if self.img_arr is None:
            return 0, 0

        img_arr = self.img_arr.reshape((1,) + self.img_arr.shape)
        ultrasonic_arr = np.array([self.ultrasonic_front_distance, self.ultrasonic_front_left_distance, self.ultrasonic_front_right_distance]).reshape(1, self.num_ultrasonic_inputs)

        with self.graph.as_default():
            steering, throttle = self.model.predict([img_arr, ultrasonic_arr])
        #print('throttle', throttle)
        #angle_certainty = max(angle_binned[0])
        angle_unbinned = dk.utils.linear_unbin(steering)
        angle_nn = angle_unbinned
        angle_final = angle_unbinned
        throttle_nn = throttle[0][0]
        throttle_final = throttle[0][0]

        if self.obstacle == Constant.OBSTACLE_ACTION_STOP:
            throttle_final = 0.0
            print('stop due to obstacle that cannot be avoided')
        else:
            if self.fuzzy.checkData(angle_nn, self.ultrasonic_front_left_distance, self.ultrasonic_front_distance, self.ultrasonic_front_right_distance) == True:

                # evaluate each row by defuzzification
                angle_fuzzy = self.fuzzy.defuzzify()
                angle_final = angle_fuzzy

                str = 'timestamp: {:.6f}, fuzzy input: {:.2f}, fuzzy output: {:.2f}, left: {:.2f}, centre: {:.2f}, right: {:.2f}, throttle: {:.2f}'
                str = str.format(time.time(), angle_nn, angle_fuzzy, self.ultrasonic_front_left_distance, self.ultrasonic_front_distance, self.ultrasonic_front_right_distance, throttle_nn)
                print(str)

        return angle_final, throttle_final

    def update(self):
        while self.on:
            self.pilot_angle, self.pilot_throttle = self.predict()
            time.sleep(0.05)
			
    def run(self, img_arr, ultrasonic_front_distance, ultrasonic_front_left_distance, ultrasonic_front_right_distance, obstacle):
        self.img_arr = img_arr
        self.ultrasonic_front_distance = ultrasonic_front_distance
        self.ultrasonic_front_left_distance = ultrasonic_front_left_distance
        self.ultrasonic_front_right_distance = ultrasonic_front_right_distance
        self.obstacle = obstacle
		
        self.pilot_angle, self.pilot_throttle = self.predict()
		
        return self.pilot_angle, self.pilot_throttle
		
    def run_threaded(self, img_arr, ultrasonic_front_distance, ultrasonic_front_left_distance, ultrasonic_front_right_distance, obstacle):
        self.img_arr = img_arr
        self.ultrasonic_front_distance = ultrasonic_front_distance
        self.ultrasonic_front_left_distance = ultrasonic_front_left_distance
        self.ultrasonic_front_right_distance = ultrasonic_front_right_distance
        self.obstacle = obstacle
		
        return self.pilot_angle, self.pilot_throttle

    def shutdown(self):
        self.on = False
    
class KerasUltrasonicSensors(KerasPilot):
    def __init__(self, model=None, num_ultrasonic_inputs = 3, *args, **kwargs):
        super(KerasUltrasonicSensors, self).__init__(*args, **kwargs)
        self.num_ultrasonic_inputs = num_ultrasonic_inputs
        self.pilot_angle = 0
        self.pilot_throttle = 0
        self.img_arr = None
        self.ultrasonic_front_distance = 0
        self.ultrasonic_front_left_distance = 0
        self.ultrasonic_front_right_distance = 0
        self.obstacle = 0

        if model:
            self.model = model
        else:
            self.model = default_ultrasonicSensors(num_ultrasonic_inputs = num_ultrasonic_inputs)

        self.on = True
		
    def predict(self):
        if self.img_arr is None:
            return 0, 0

        img_arr = self.img_arr.reshape((1,) + self.img_arr.shape)
        ultrasonic_arr = np.array([self.ultrasonic_front_distance, self.ultrasonic_front_left_distance, self.ultrasonic_front_right_distance]).reshape(1, self.num_ultrasonic_inputs)

        with self.graph.as_default():
            steering, throttle = self.model.predict([img_arr, ultrasonic_arr])

        angle_unbinned = dk.utils.linear_unbin(steering)
        angle_nn = angle_unbinned
        angle_final = angle_unbinned
        throttle_nn = throttle[0][0]
        throttle_final = throttle[0][0]

        if self.obstacle == Constant.OBSTACLE_ACTION_STOP:
            throttle_final = 0.0
            print('stop due to obstacle that cannot be avoided')
			
        return angle_final, throttle_final

    def update(self):
        while self.on:
            self.pilot_angle, self.pilot_throttle = self.predict()
            time.sleep(0.05)
			
    def run(self, img_arr, ultrasonic_front_distance, ultrasonic_front_left_distance, ultrasonic_front_right_distance, obstacle):
        self.img_arr = img_arr
        self.ultrasonic_front_distance = ultrasonic_front_distance
        self.ultrasonic_front_left_distance = ultrasonic_front_left_distance
        self.ultrasonic_front_right_distance = ultrasonic_front_right_distance
        self.obstacle = obstacle
		
        self.pilot_angle, self.pilot_throttle = self.predict()
		
        return self.pilot_angle, self.pilot_throttle
		
    def run_threaded(self, img_arr, ultrasonic_front_distance, ultrasonic_front_left_distance, ultrasonic_front_right_distance, obstacle):
        self.img_arr = img_arr
        self.ultrasonic_front_distance = ultrasonic_front_distance
        self.ultrasonic_front_left_distance = ultrasonic_front_left_distance
        self.ultrasonic_front_right_distance = ultrasonic_front_right_distance
        self.obstacle = obstacle
		
        return self.pilot_angle, self.pilot_throttle

    def shutdown(self):
        self.on = False
		
def default_categorical():
    from keras.layers import Input, Dense, merge
    from keras.models import Model
    from keras.layers import Convolution2D, MaxPooling2D, Reshape, BatchNormalization
    from keras.layers import Activation, Dropout, Flatten, Dense
    
    img_in = Input(shape=(120, 160, 3), name='img_in')                      # First layer, input layer, Shape comes from camera.py resolution, RGB
    x = img_in
    x = Convolution2D(24, (5,5), strides=(2,2), activation='relu')(x)       # 24 features, 5 pixel x 5 pixel kernel (convolution, feauture) window, 2wx2h stride, relu activation
    x = Convolution2D(32, (5,5), strides=(2,2), activation='relu')(x)       # 32 features, 5px5p kernel window, 2wx2h stride, relu activatiion
    x = Convolution2D(64, (5,5), strides=(2,2), activation='relu')(x)       # 64 features, 5px5p kernal window, 2wx2h stride, relu
    x = Convolution2D(64, (3,3), strides=(2,2), activation='relu')(x)       # 64 features, 3px3p kernal window, 2wx2h stride, relu
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)       # 64 features, 3px3p kernal window, 1wx1h stride, relu

    # Possibly add MaxPooling (will make it less sensitive to position in image).  Camera angle fixed, so may not to be needed

    x = Flatten(name='flattened')(x)                                        # Flatten to 1D (Fully connected)
    x = Dense(100, activation='relu')(x)                                    # Classify the data into 100 features, make all negatives 0
    x = Dropout(.1)(x)                                                      # Randomly drop out (turn off) 10% of the neurons (Prevent overfitting)
    x = Dense(50, activation='relu')(x)                                     # Classify the data into 50 features, make all negatives 0
    x = Dropout(.1)(x)                                                      # Randomly drop out 10% of the neurons (Prevent overfitting)
    #categorical output of the angle
    angle_out = Dense(15, activation='softmax', name='angle_out')(x)        # Connect every input with every output and output 15 hidden units. Use Softmax to give percentage. 15 categories and find best one based off percentage 0.0-1.0
    
    #continous output of throttle
    throttle_out = Dense(1, activation='relu', name='throttle_out')(x)      # Reduce to 1 number, Positive number only
    
    model = Model(inputs=[img_in], outputs=[angle_out, throttle_out])
    model.compile(optimizer='adam',
                  loss={'angle_out': 'categorical_crossentropy', 
                        'throttle_out': 'mean_absolute_error'},
                  loss_weights={'angle_out': 0.9, 'throttle_out': .001})

    return model



def default_linear():
    from keras.layers import Input, Dense, merge
    from keras.models import Model
    from keras.layers import Convolution2D, MaxPooling2D, Reshape, BatchNormalization
    from keras.layers import Activation, Dropout, Flatten, Dense
    
    img_in = Input(shape=(120,160,3), name='img_in')
    x = img_in
    x = Convolution2D(24, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(32, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)
    
    x = Flatten(name='flattened')(x)
    x = Dense(100, activation='linear')(x)
    x = Dropout(.1)(x)
    x = Dense(50, activation='linear')(x)
    x = Dropout(.1)(x)
    #categorical output of the angle
    angle_out = Dense(1, activation='linear', name='angle_out')(x)
    
    #continous output of throttle
    throttle_out = Dense(1, activation='linear', name='throttle_out')(x)
    
    model = Model(inputs=[img_in], outputs=[angle_out, throttle_out])
    
    
    model.compile(optimizer='adam',
                  loss={'angle_out': 'mean_squared_error', 
                        'throttle_out': 'mean_squared_error'},
                  loss_weights={'angle_out': 0.5, 'throttle_out': .5})

    return model



def default_n_linear(num_outputs):
    from keras.layers import Input, Dense, merge
    from keras.models import Model
    from keras.layers import Convolution2D, MaxPooling2D, Reshape, BatchNormalization
    from keras.layers import Activation, Dropout, Flatten, Cropping2D, Lambda
    
    img_in = Input(shape=(120,160,3), name='img_in')
    x = img_in
    x = Cropping2D(cropping=((60,0), (0,0)))(x) #trim 60 pixels off top
    x = Lambda(lambda x: x/127.5 - 1.)(x) # normalize and re-center
    x = Convolution2D(24, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(32, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (5,5), strides=(1,1), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)
    
    x = Flatten(name='flattened')(x)
    x = Dense(100, activation='relu')(x)
    x = Dropout(.1)(x)
    x = Dense(50, activation='relu')(x)
    x = Dropout(.1)(x)

    outputs = [] 
    
    for i in range(num_outputs):
        outputs.append(Dense(1, activation='linear', name='n_outputs' + str(i))(x))
        
    model = Model(inputs=[img_in], outputs=outputs)
    
    
    model.compile(optimizer='adam',
                  loss='mse')

    return model



def default_imu(num_outputs, num_imu_inputs):
    '''
    Notes: this model depends on concatenate which failed on keras < 2.0.8
    '''

    from keras.layers import Input, Dense
    from keras.models import Model
    from keras.layers import Convolution2D, MaxPooling2D, Reshape, BatchNormalization
    from keras.layers import Activation, Dropout, Flatten, Cropping2D, Lambda
    from keras.layers.merge import concatenate
    
    img_in = Input(shape=(120,160,3), name='img_in')
    imu_in = Input(shape=(num_imu_inputs,), name="imu_in")
    
    x = img_in
    x = Cropping2D(cropping=((60,0), (0,0)))(x) #trim 60 pixels off top
    #x = Lambda(lambda x: x/127.5 - 1.)(x) # normalize and re-center
    x = Convolution2D(24, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(32, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)
    x = Flatten(name='flattened')(x)
    x = Dense(100, activation='relu')(x)
    x = Dropout(.1)(x)
    
    y = imu_in
    y = Dense(14, activation='relu')(y)
    y = Dense(14, activation='relu')(y)
    y = Dense(14, activation='relu')(y)
    
    z = concatenate([x, y])
    z = Dense(50, activation='relu')(z)
    z = Dropout(.1)(z)
    z = Dense(50, activation='relu')(z)
    z = Dropout(.1)(z)

    outputs = [] 
    
    for i in range(num_outputs):
        outputs.append(Dense(1, activation='linear', name='out_' + str(i))(z))
        
    model = Model(inputs=[img_in, imu_in], outputs=outputs)
    
    model.compile(optimizer='adam',
                  loss='mse')
    
    return model
	
def default_ultrasonicSensors(num_ultrasonic_inputs):

    from keras.layers import Input, Dense
    from keras.models import Model
    from keras.layers import Convolution2D, MaxPooling2D, Reshape, BatchNormalization
    from keras.layers import Activation, Dropout, Flatten, Cropping2D, Lambda
    from keras.layers.merge import concatenate
    
    img_in = Input(shape=(120,160,3), name='img_in')
    ultrasonic_in = Input(shape=(num_ultrasonic_inputs,), name="ultrasonic_in")
    
    x = img_in
    x = Cropping2D(cropping=((60,0), (0,0)))(x) #trim 60 pixels off top
    #x = Lambda(lambda x: x/127.5 - 1.)(x) # normalize and re-center
    x = Convolution2D(24, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(32, (5,5), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(2,2), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)
    x = Flatten(name='flattened')(x)
    x = Dense(100, activation='relu')(x)
    x = Dropout(.1)(x)
    x = Dense(50, activation='relu')(x)
    x = Dropout(.1)(x)

    #categorical output of the angle
    angle_out = Dense(15, activation='softmax', name='angle_out')(x)        # Connect every input with every output and output 15 hidden units. Use Softmax to give percentage. 15 categories and find best one based off percentage 0.0-1.0
    
    #continous output of throttle
    throttle_out = Dense(1, activation='relu', name='throttle_out')(x)      # Reduce to 1 number, Positive number only

    model = Model(inputs=[img_in], outputs=[angle_out, throttle_out])
    
    model.compile(optimizer='adam',
                  loss={'angle_out': 'categorical_crossentropy', 
                        'throttle_out': 'mean_absolute_error'},
                  loss_weights={'angle_out': 0.9, 'throttle_out': .001})
    return model

def default_categoricalCustom():
    from keras.layers import Input, Dense, merge
    from keras.models import Model
    from keras.layers import Convolution2D, MaxPooling2D, Reshape, BatchNormalization
    from keras.layers import Activation, Dropout, Flatten, Dense, Cropping2D
    
    img_in = Input(shape=(120, 160, 3), name='img_in')                      # First layer, input layer, Shape comes from camera.py resolution, RGB
    x = img_in
    x = Cropping2D(cropping=((60,0), (0,0)))(x)                             #trim 60 pixels off top
    x = Convolution2D(24, (5,5), strides=(2,2), activation='relu')(x)       # 24 features, 5 pixel x 5 pixel kernel (convolution, feauture) window, 2wx2h stride, relu activation
    x = Convolution2D(32, (5,5), strides=(2,2), activation='relu')(x)       # 32 features, 5px5p kernel window, 2wx2h stride, relu activatiion
    x = Convolution2D(64, (3,3), strides=(2,2), activation='relu')(x)       # 64 features, 5px5p kernal window, 2wx2h stride, relu
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)       # 64 features, 3px3p kernal window, 2wx2h stride, relu
    x = Convolution2D(64, (3,3), strides=(1,1), activation='relu')(x)       # 64 features, 3px3p kernal window, 1wx1h stride, relu

    # Possibly add MaxPooling (will make it less sensitive to position in image).  Camera angle fixed, so may not to be needed

    x = Flatten(name='flattened')(x)                                        # Flatten to 1D (Fully connected)
    x = Dense(100, activation='relu')(x)                                    # Classify the data into 100 features, make all negatives 0
    x = Dropout(.1)(x)                                                      # Randomly drop out (turn off) 10% of the neurons (Prevent overfitting)
    x = Dense(50, activation='relu')(x)                                     # Classify the data into 50 features, make all negatives 0
    x = Dropout(.1)(x)                                                      # Randomly drop out 10% of the neurons (Prevent overfitting)
    #categorical output of the angle
    angle_out = Dense(15, activation='softmax', name='angle_out')(x)        # Connect every input with every output and output 15 hidden units. Use Softmax to give percentage. 15 categories and find best one based off percentage 0.0-1.0
    
    #continous output of throttle
    throttle_out = Dense(1, activation='relu', name='throttle_out')(x)      # Reduce to 1 number, Positive number only
    
    model = Model(inputs=[img_in], outputs=[angle_out, throttle_out])
    model.compile(optimizer='adam',
                  loss={'angle_out': 'categorical_crossentropy', 
                        'throttle_out': 'mean_absolute_error'},
                  loss_weights={'angle_out': 0.9, 'throttle_out': .001})

    return model