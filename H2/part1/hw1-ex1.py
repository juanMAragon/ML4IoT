import argparse
import numpy as np
import os
import pandas as pd
import tensorflow as tf
from tensorflow import keras
import tensorflow_model_optimization as tfmot
import tensorflow.lite as tflite
import zlib
import sys
import shutil

# parser = argparse.ArgumentParser()
# parser.add_argument('--version', type=str, required=True)
# args = parser.parse_args()
# version = args.version

## Parameter Set
input_width = 6
output_width = 6
EPOCHS = 20
LEARNING_RATE = 0.001
ALPHA = 0.12
LABEL_OPTIONS = 2

#------------------------------------------------------------------------------------------
class WindowGenerator:
    def __init__(self, input_width, label_options, mean, std):
        self.input_width = input_width
        self.label_options = label_options
        self.mean = tf.reshape(tf.convert_to_tensor(mean), [1, 1, 2])
        self.std = tf.reshape(tf.convert_to_tensor(std), [1, 1, 2])

    def split_window(self, features):
        input_indeces = np.arange(self.input_width)
        inputs = features[:, :-6, :]
            
        labels = features[:, -6:, :]
        num_labels = 2

        inputs.set_shape([None, self.input_width, 2])
        # vedere se funge
        labels.set_shape([None, self.input_width, num_labels])

        return inputs, labels

    def normalize(self, features):
        features = (features - self.mean) / (self.std + 1.e-6)

        return features

    def preprocess(self, features):
        inputs, labels = self.split_window(features)
        inputs = self.normalize(inputs)

        return inputs, labels

    def make_dataset(self, data, train):
        ds = tf.keras.preprocessing.timeseries_dataset_from_array(
                data=data,
                # Targets None because we have the targets incorporated in the dataset
                targets=None,
                sequence_length=input_width+6,
                sequence_stride=1,
                batch_size=32)
        ds = ds.map(self.preprocess)
        ds = ds.cache()
        if train is True:
            ds = ds.shuffle(100, reshuffle_each_iteration=True)

        return ds


class MMAE(tf.keras.metrics.Metric):
    def __init__(self, name='MMAE', **kwargs):
        super(MMAE, self).__init__(name=name, **kwargs)
        self.total = self.add_weight(name='total', initializer='zeros', shape=(2, ))
        self.count = self.add_weight(name='count', initializer='zeros')

    def reset_state(self):
        self.count.assign(tf.zeros_like(self.count))
        self.total.assign(tf.zeros_like(self.total))
        return

    def update_state(self, y_true, y_pred, sample_weight=None):
        error = tf.abs(y_pred - y_true)
        error = tf.reduce_mean(error, axis=[0,1])
        self.total.assign_add(error)
        self.count.assign_add(1)
        return

    def result(self):
        result = tf.math.divide_no_nan(self.total, self.count)
        return result

#---------------------------------------------------------------------------------------------

## Data Prepration, split, loader

seed = 42
tf.random.set_seed(seed)
np.random.seed(seed)

zip_path = tf.keras.utils.get_file(
    origin='https://storage.googleapis.com/tensorflow/tf-keras-datasets/jena_climate_2009_2016.csv.zip',
    fname='jena_climate_2009_2016.csv.zip',
    extract=True,
    cache_dir='.', cache_subdir='data')
csv_path, _ = os.path.splitext(zip_path)
df = pd.read_csv(csv_path)

column_indices = [2, 5]
columns = df.columns[column_indices]
data = df[columns].values.astype(np.float32)

n = len(data)
train_data = data[0:int(n*0.7)]
val_data = data[int(n*0.7):int(n*0.9)]
test_data = data[int(n*0.9):]

mean = train_data.mean(axis=0)
std = train_data.std(axis=0)

generator = WindowGenerator(input_width, output_width, mean, std)
train_ds = generator.make_dataset(train_data, True)
val_ds = generator.make_dataset(val_data, False)
test_ds = generator.make_dataset(test_data, False)


## Model
model = keras.Sequential([
    keras.layers.Flatten(input_shape=(6, 2)),
    keras.layers.Dense(units=int(128*ALPHA), activation='relu', name='first_dense'),
    keras.layers.Dense(units=int(128*ALPHA), activation='relu', name='second_dense'),
    keras.layers.Dense(12, name='output'),
    keras.layers.Reshape([6, 2])
    ])

model_dir = './models/model_{}'.format(str(LABEL_OPTIONS))

## Training 

optimizer = tf.keras.optimizers.Adam(learning_rate=LEARNING_RATE)

model.compile(optimizer=optimizer,
              loss=tf.keras.losses.MSE,
              metrics=[MMAE()])


history = model.fit(
    train_ds,
    batch_size=32,
    epochs=EPOCHS,
    validation_data=(val_ds),
)

loss, error = model.evaluate(test_ds, verbose=2)
print(f"Score for temperature: {error[0]}")
print(f"Score for humidity: {error[1]}")


run_model = tf.function(lambda x: model(x))
concrete_func = run_model.get_concrete_function(tf.TensorSpec([1, 6, 2],
tf.float32))
model.save(model_dir, signatures=concrete_func)

converter = tf.lite.TFLiteConverter.from_saved_model(model_dir)
converter.optimizations = [tf.lite.Optimize.DEFAULT]
converter.target_spec.supported_types = [tf.float16]

tflite_model = converter.convert()

version = 1
tflite_model_dir = './Group17_th_{}.tflite.zlib'.format(version)

with open(tflite_model_dir, 'wb') as fp:
    tflite_compressed = zlib.compress(tflite_model)
    fp.write(tflite_compressed)

print(f"Size of compressed tflite model: {os.path.getsize(tflite_model_dir)/1024} kB")