#!/usr/bin/env python
# coding: utf-8

# In[1]:


#from CAML_generic_autokeras import AutoMLClassifier, AutoMLRegressor
# some_file.py
import sys
# insert at 1, 0 is the script path (or '' in REPL)
sys.path.insert(1, 'main_classes/')

from CAML_generic_automl_classes import *


## Import Libraries

# General system libraries
import os
import sys
import shutil
import math
import pickle
import itertools
import numpy as np
import pandas as pd
from time import time

# Multiprocessing
import multiprocessing

# pysster Lib
from pysster import utils
from pysster.Data import Data
#     Installation:
#     cd to src/pysster folder in terminal and then execute: 
#     python setup.py install --user

# Import sklearn libs
from sklearn import preprocessing
from sklearn.model_selection import train_test_split
from sklearn.metrics import explained_variance_score, mean_absolute_error
from sklearn.metrics import mean_squared_error, mean_squared_log_error
from sklearn.metrics import median_absolute_error, r2_score
from sklearn.model_selection import KFold
from sklearn.metrics import roc_curve, auc
from sklearn.metrics import matthews_corrcoef as mcc

# Math & Visualization Libs
import seaborn as sns
import matplotlib.pyplot as plt
from scipy import stats
from IPython.display import Image

# Tensorflow libs
import tensorflow as tf 

# Import Keras
import keras
from keras import optimizers, applications, regularizers
from keras import backend as K
from keras.models import Sequential, load_model
from keras.models import model_from_json, load_model
from keras.layers import Activation, Conv1D, Conv2D, Reshape, BatchNormalization, Dropout, Flatten, Dense, merge, Input, Lambda, InputLayer, Convolution2D, MaxPooling1D, MaxPooling2D, ZeroPadding2D, Bidirectional, concatenate
from keras.layers.recurrent import LSTM
from keras.layers.embeddings import Embedding
from keras.preprocessing import image
from keras.preprocessing.image import ImageDataGenerator, img_to_array, load_img
from keras.applications.imagenet_utils import decode_predictions, preprocess_input
from keras.wrappers.scikit_learn import KerasClassifier
from keras.wrappers.scikit_learn import KerasRegressor
from keras.models import Model
from keras.optimizers import Adam
from keras.utils import np_utils
from keras.utils import multi_gpu_model
from keras.utils import plot_model
from keras.utils.np_utils import to_categorical
from tensorflow.keras.callbacks import EarlyStopping
#from keras.callbacks import EarlyStopping
#from keras_tqdm import TQDMNotebookCallback
from keras.callbacks import ModelCheckpoint
from CAML_interpret_helpers import plot_mutagenesis, plot_rawseqlogos, plot_activation_maps, plot_saliency_maps, plot_seqlogos
from CAML_integrated_design_helpers import integrated_design

import yaml
from graphviz import Digraph

# Warnings
import warnings
warnings.filterwarnings("ignore")

#Visualization


# In[ ]:

# functions that can be called by both deepswarm regression and classifiers
def convert_deepswarm_input(df_data_input, df_data_output, pad_seqs, augment_data, sequence_type): 
    df_data_input, df_data_output, scrambled_df_data_input, alph = AutoMLBackend.clean_input(df_data_input, df_data_output, pad_seqs, augment_data, sequence_type)
    
    oh_data_input, numerical_data_input = AutoMLBackend.onehot_seqlist(df_data_input, sequence_type, alph, model_type = 'deepswarm')
    scrambled_oh_data_input, scrambled_numerical_data_input = AutoMLBackend.onehot_seqlist(scrambled_df_data_input, sequence_type, alph, model_type = 'deepswarm')
    print('Confirmed: Scrambled control generated.')

    return numerical_data_input, oh_data_input, df_data_output, scrambled_numerical_data_input, scrambled_oh_data_input, alph

def print_summary(model, output_file_path):
    # adapted from: https://stackoverflow.com/questions/41665799/keras-model-summary-object-to-string
    with open(output_file_path,'w') as fh:
        # Pass the file handle in as a lambda function to make it callable
        model.summary(print_fn=lambda x: fh.write(x + '\n'))

def reset_weights(model):
    session = K.get_session()
    for layer in model.layers: 
        if hasattr(layer, 'kernel_initializer'):
            layer.kernel.initializer.run(session=session)
        
# [Function] Train deploy model using all data
def fit_final_model(topology_path, num_epochs, compile_model, X, y): 
    # fit on all of the data 
    # NOTE: b/c deepswarm, need to run w/ custom data formatting and need to reformat deepswarm output file
    # similar to method used per fold 
    
    training_features = np.array(X)[:]
    training_target = y[:]
    
    # Recreate the exact same model purely from the file
    model = tf.keras.models.load_model(topology_path)
    compile_model(model)
    
    # Train topology for N epochs in deepswarm
    validation_spit_init = 0.1
    callbacks_list = [EarlyStopping(monitor='val_loss', patience=math.ceil(num_epochs*0.1), verbose = False)]     # Callback to be used for checkpoint generation and early stopping
    model_history = model.fit(training_features, training_target, validation_split=validation_spit_init, epochs=num_epochs, callbacks=callbacks_list)
        
    return model 



# In[3]:


class DeepSwarmClassification(AutoMLClassifier): 
    
    def __init__(self, data_path, model_folder, output_folder, max_runtime, num_folds, sequence_type, do_auto_bin=True, bin_threshold=None, verbosity=0, yaml_params={}, num_final_epochs=50, input_col = 'seq', target_col = 'target', pad_seqs = 'max', augment_data = 'none', multiclass = False, dataset_robustness = False, run_interpretation = True, interpret_params = {}, run_design = True, design_params = {}):
        AutoMLClassifier.__init__(self, data_path, model_folder, output_folder, max_runtime, num_folds, do_auto_bin, bin_threshold, input_col, target_col)
        self.verbosity = verbosity 
        # pull out yaml parameters and set defaults if not specified
        self.max_depth = yaml_params.get('max_depth', 3) 
        self.ant_count = yaml_params.get('ant_count', 4) 
        self.epochs = yaml_params.get('epochs', 5) 
        self.num_epochs=num_final_epochs
        self.best_model = None 
        self.topology_path = None 
        self.input_col = input_col
        self.target_col = target_col
        self.pad_seqs = pad_seqs
        self.augment_data = augment_data
        self.sequence_type = sequence_type
        self.multiclass = multiclass
        self.dataset_robustness = dataset_robustness
        self.run_interpretation = run_interpretation
        self.interpret_params = interpret_params
        self.run_design = run_design
        self.design_params = design_params

    
    def convert_input(self): 
        return convert_deepswarm_input(self.df_data_input, self.df_data_output, self.pad_seqs, self.augment_data, self.sequence_type)

    def mcc(y_true, y_pred):
        mcc = mcc(y_true, y_pred)
        return tf.reduce_mean(mcc, axis=-1)  # Note the `axis=-1`
    
    def update_yaml_file(self, input_shape):
        # file editing
        # help from: https://stackoverflow.com/questions/6866600/how-to-parse-read-a-yaml-file-into-a-python-object 

        settings_folder = './settings/'# + 'default_classification.yaml'
        with open(settings_folder + 'default.yaml') as f:
            data_map = yaml.load(f)
        data_map['DeepSwarm']['metrics']='accuracy'
        if self.multiclass:
            vals = [item for sublist in self.df_data_output.values for item in sublist]
            lenvals = len(set(vals))
            data_map['DeepSwarm']['metrics']='categorical_crossentropy'
            data_map['DeepSwarm']['backend']['loss'] = 'categorical_crossentropy'
            data_map['Nodes']['OutputNode']['attributes']['output_size']= [lenvals] # for two-class classification 
            data_map['Nodes']['OutputNode']['attributes']['shape']= [lenvals] # for two-class classification 
        else:
            data_map['DeepSwarm']['metrics']='binary_crossentropy'
            data_map['DeepSwarm']['backend']['loss'] = 'binary_crossentropy'
            data_map['Nodes']['OutputNode']['attributes']['output_size']= [2] # for two-class classification 
            data_map['Nodes']['OutputNode']['attributes']['shape']= [2] # for two-class classification 
        
        data_map['max_depth'] = self.max_depth
        data_map['DeepSwarm']['aco']['ant_count'] = self.ant_count
        data_map['DeepSwarm']['backend']['epochs'] = self.epochs
        data_map['DeepSwarm']['max_depth'] = self.max_depth
        #data_map['DeepSwarm']['save_folder'] = './models/deepswarm/classification/'   
        data_map['DeepSwarm']['save_folder']  = self.model_folder    
        data_map['Nodes']['InputNode']['attributes']['shape']= [input_shape]#[(30,4,1)]
        data_map['Nodes']['OutputNode']['attributes']['activation']= ['Softmax']

        # changing architecture space searched
        data_map['Nodes']['Conv2DNode']['attributes']['filter_count']= [8,16,32,64]#
        data_map['Nodes']['Conv2DNode']['attributes']['kernel_size']= [1,3,5,7]#
        data_map['Nodes']['DenseNode']['attributes']['output_size']= [30,64,128, 256]#

        with open(settings_folder + 'default.yaml', "w") as f:
            yaml.dump(data_map, f)
    
    def compile_model(self,model):
        if self.multiclass:
            optimizer_parameters = {
                'optimizer': 'adam',
                'loss': 'categorical_crossentropy',
                'metrics': ['accuracy', 'categorical_crossentropy'],
            }
        else:
            optimizer_parameters = {
                'optimizer': 'adam',
                'loss': 'binary_crossentropy',
                'metrics': ['accuracy', 'binary_crossentropy'],
            } 

        # If user specified custom optimizer, use it instead of the default one
        model.compile(**optimizer_parameters)
    
    def find_best_architecture(self, X, y):

        from deepswarm.backends import Dataset, TFKerasBackend
        from deepswarm.deepswarm import DeepSwarm

        # Ensure replicability (Seed needed here)
        train_size = 0.6
        seed = 7
        np.random.seed(seed)

        if not os.path.isdir(self.model_folder):
            os.makedirs(self.model_folder)

        # DEEPSWARM MODEL FINDING AND TRAINING
        # NOTE: see for example: https://github.com/Pattio/DeepSwarm/blob/master/examples/mnist.py 

        # Prepare Dataset object for Deepswarm
        x_train, x_test, y_train, y_test = train_test_split(X, y, test_size = 1-train_size, random_state = seed)
        x_train = x_train.reshape(x_train.shape[0], x_train.shape[1], x_train.shape[2], 1)
        x_test = x_test.reshape(x_test.shape[0], x_train.shape[1], x_train.shape[2], 1)
        val_split_size = 0.1

        normalized_dataset = Dataset(
            training_examples=x_train,
            training_labels=y_train,
            testing_examples=x_test,
            testing_labels=y_test,
            validation_split=val_split_size,
        )

        #Create backend responsible for training & validating
        backend = TFKerasBackend(dataset=normalized_dataset)
        if self.verbosity>0: print('Deepswarm TFK Backend: Created!')

        # Create DeepSwarm object responsible for optimization
        deepswarm = DeepSwarm(backend=backend)
        if self.verbosity>0: print('Deepswarm Object: Created!')

        # Find the topology for a given dataset
        topology = deepswarm.find_topology()
        if self.verbosity>0: print('Deepswarm Topology: Found!')

        # Evaluate discovered topology
        deepswarm.evaluate_topology(topology)
        if self.verbosity>0: print('Deepswarm Preliminary Topology Evaluation: Done!')

        # Train topology for N epochs
        base_model = deepswarm.train_topology(topology, self.num_epochs)
        if self.verbosity>0: print('Deepswarm Topology Base Training: Completed!')

        # Evaluate the Base trained model
        deepswarm.evaluate_topology(base_model)
        if self.verbosity>0: print('Deepswarm Topology Evaluation: Completed!')

        print('folder: ' + self.model_folder)
        # Save the DeepSwarm base trained model using optimal topology
        base_model.save(self.model_folder + 'deepswarm_base_model.h5')
        
        # Save the DeepSwarm optimal topology with reinitialized weights
        topology = base_model
        reset_weights(topology)

        topology.save(self.model_folder + 'deepswarm_topology.h5')

        # Display saving message
        if self.verbosity>0: print('Deepswarm Results: Saved!')
            
        #del Dataset, DeepSwarm, TFKerasBackend
            
        return backend, deepswarm, topology, base_model # return destination of model and reload/retrain per fold 
        
    
    def train_architecture_kfold(self, X, y, transform_obj,seed,alph): 
        
        # run kfold cross-validation over the best model from autokeras
        # model_path is the path of the best pipeline from autokeras

        # set-up k-fold system 
        # default num folds = 10 (can be specified by user)
        kfold = KFold(n_splits=self.num_folds, shuffle=True, random_state=seed)

        # keep track of metrics per fold
        cv_scores = []
        # save predictions
        predictions = [] # in order of folds
        true_targets = [] # in order of folds
        compiled_seqs= []

        #print('AAAAAAAAAAAAAAAAAAA')
        #print(X)
        #print(y)
        fold_count = 1 # keep track of the fold (when saving models)
        for train, test in kfold.split(X, y):
            print('Current fold:', fold_count)

                # Allocate fold train/test for  exported deepswarm pipeline 
            training_features = np.array(X)[train]
            training_target = y[train]
            testing_features = np.array(X)[test]
            testing_target = y[test]

            # Recreate the exact same model purely from the file
            topology_path = self.model_folder + 'deepswarm_topology.h5'
            self.topology_path = topology_path
            model = tf.keras.models.load_model(topology_path)
            self.compile_model(model)

            # Train topology for N epochs in deepswarm
            validation_spit_init = 0.1
            callbacks_list = [EarlyStopping(monitor='val_loss', patience=math.ceil(self.num_epochs*0.1), verbose = False)]     # Callback to be used for checkpoint generation and early stopping 
            model_history = model.fit(training_features, training_target, validation_split=validation_spit_init, epochs=self.num_epochs, callbacks=callbacks_list)

            # Calculate model predictions for all fold testing samples
            results = model.predict(testing_features)

            # save metrics and key attributes of each fold 
            model_eval_metrics = model.evaluate(testing_features,testing_target)

            # need to inverse transform (if user wanted output to be transformed originally)
            true_targets.extend(testing_target)
            predictions.extend(results) # keep y_true, y_pred
            compiled_seqs.extend(AutoMLBackend.reverse_onehot2seq(np.array(X)[test], alph, self.sequence_type, numeric = True))
            resulting_metrics, metric_names = self.classification_performance_eval(self.output_folder +'deepswarm_', testing_target, np.array(results), str(fold_count))#np.expand_dims(results,1), str(fold_count))
            cv_scores.append(resulting_metrics)
            del model
            fold_count += 1
        return np.array(cv_scores), predictions, true_targets, compiled_seqs 

    # [Function] Train deploy model using all data
    def fit_final_model(self, X, y): 
        # get best model
        # need to read-in from outputted file and instantiate the pipeline in current cell
        model = fit_final_model(self.topology_path,  self.num_epochs, self.compile_model, X, y)
        # can load as follows: model = pickle_from_file(final_model_path)
        self.best_model = model                                                
        return model # final fitted model                                                                 
                                                                
    def run_system(self): 

        start1 = time()
        print('Conducting architecture search now...')

        with suppress_stdout():

            # if user has specified an output folder that doesn't exist, create it 
            if not os.path.isdir(self.output_folder):
                os.makedirs(self.output_folder)

            # transform input
            numerical_data_input, oh_data_input, df_data_output, scrambled_numerical_data_input, scrambled_oh_data_input, alph = self.convert_input()
            self.df_data_output = df_data_output

            # transform output (target) into bins for classification
            transformed_output, transform_obj = self.transform_target(self.multiclass)

            # now, we have completed the pre-processing needed to feed our data into deepswarm
            # deepswarm input: numerical_data_input
            # deepswarm output: transformed_output
            X = numerical_data_input
            #print(numerical_data_input)
            #y = to_categorical(transformed_output) 
            y = to_categorical(transformed_output)

             # Update the default.yaml settings file
            self.update_yaml_file(input_shape= (np.shape(X)[1], np.shape(X)[2], 1))
            
            # run deepswarm to find best model
            backend, deepswarm, topology, base_model  = self.find_best_architecture(X, y,)

            seed = 7
            # Run kfold cv over the resulting pipeline
            #topology_path = model_folder + 'deepswarm_topology.h5'
            cv_scores, compiled_preds, compiled_true, compiled_seqs = self.train_architecture_kfold(X, y, transform_obj,seed,alph)

            # if failed to execute properly, just stop rest of execution
            if cv_scores is None: return None, None

            # now, get the average scores (find avg metric and std, to show variability) across folds 
            avg_metric_folds = np.mean(cv_scores, axis = 0) # avg over columns 
            std_metric_folds = np.std(cv_scores, axis = 0) # avg over columns 
            cv_scores = cv_scores.transpose()
            # now get the compiled metric and generate an overall plot 
            compiled_metrics, metric_names = self.classification_performance_eval(self.output_folder+'deepswarm_',np.array(compiled_true), np.array(compiled_preds), file_tag = 'compiled')#np.expand_dims(compiled_preds,1), file_tag = 'compiled')

        print('Metrics over folds: ')
        for i, metric in enumerate(metric_names):
            print('\tAverage ' + metric + ': ', avg_metric_folds[i])
            print('\tStd of ' + metric + ': ', std_metric_folds[i])
        results_file_path = self.write_results(metric_names,avg_metric_folds, std_metric_folds, compiled_metrics, cv_scores, scrambled=False)

        end1 = time()
        runtime_stat_time = 'Elapsed time for autoML search : ' + str(np.round(((end1 - start1) / 60), 2))  + ' minutes'
        AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')
        start2 = time()

        print('Testing scrambled control now...')

        with suppress_stdout():
            if not os.path.isdir(self.output_folder + 'scrambled/'):
                os.mkdir(self.output_folder + 'scrambled/')

            # test scrambled control on best architecture
            scr_X = scrambled_numerical_data_input
            scr_cv_scores, scr_compiled_preds, scr_compiled_true, scr_compiled_seqs = self.train_architecture_kfold(scr_X, y, transform_obj,seed,alph)
            # now, get the average scores (find avg metric and std, to show variability) across folds 
            scr_avg_metric_folds = np.mean(scr_cv_scores, axis = 0) # avg over columns 
            scr_std_metric_folds = np.std(scr_cv_scores, axis = 0) # avg over columns 
            scr_cv_scores = scr_cv_scores.transpose()

            # now get the compiled metric and generate an overall plot 
            scr_compiled_metrics, _ = self.classification_performance_eval(self.output_folder+'scrambled/',np.array(scr_compiled_true), np.array(scr_compiled_preds), file_tag = 'compiled')#np.expand_dims(compiled_preds,1), file_tag = 'compiled')

        print('Scrambled metrics over folds: ')
        for i, metric in enumerate(metric_names):
            print('\tAverage ' + metric + ': ', scr_avg_metric_folds[i])
            print('\tStd of ' + metric + ': ', scr_std_metric_folds[i])

        # write results to a text file for the user to read
        scr_results_file_path = self.write_results(metric_names,scr_avg_metric_folds, scr_std_metric_folds, scr_compiled_metrics, scr_cv_scores, scrambled=True)
        
        end2 = time()
        runtime_stat_time = 'Elapsed time for scrambled control : ' + str(np.round(((end2 - start2) / 60), 2))  + ' minutes'
        AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')
        
        # dataset robustness test
        if self.dataset_robustness:
            start3 = time()

            dataset_size = len(X)
            if not os.path.isdir(self.output_folder + 'robustness/'):
                os.mkdir(self.output_folder + 'robustness/')
            while dataset_size > 1000:
                dataset_size = int(dataset_size / 2)
                print("Testing with dataset size of: " + str(dataset_size))

                with suppress_stdout():
                    cv_scores, compiled_preds, compiled_true, compiled_seqs = self.train_architecture_kfold(X[0:dataset_size], y[0:dataset_size], transform_obj,seed,alph)
                
                    # now, get the average scores (find avg metric and std, to show variability) across folds 
                    avg_metric_folds = np.mean(cv_scores, axis = 0) # avg over columns 
                    std_metric_folds = np.std(cv_scores, axis = 0) # avg over columns 
                    cv_scores = cv_scores.transpose()

                    # now get the compiled metric and generate an overall plot 
                    compiled_metrics, metric_names = self.classification_performance_eval(self.output_folder+'robustness/' + str(dataset_size) + '_', np.array(compiled_true), np.array(compiled_preds), file_tag = 'compiled')
                    # write results to a text file for the user to read
                    results_file_path = self.write_results(metric_names,avg_metric_folds, std_metric_folds, compiled_metrics, cv_scores, scrambled=False, subset = str(dataset_size))

                    scr_cv_scores, scr_compiled_preds, scr_compiled_true, scr_compiled_seqs = self.train_architecture_kfold(scr_X[0:dataset_size], y[0:dataset_size], transform_obj,seed,alph)
                    # now, get the average scores (find avg metric and std, to show variability) across folds 
                    scr_avg_metric_folds = np.mean(scr_cv_scores, axis = 0) # avg over columns 
                    scr_std_metric_folds = np.std(scr_cv_scores, axis = 0) # avg over columns 
                    scr_cv_scores = scr_cv_scores.transpose()

                    # now get the compiled metric and generate an overall plot 
                    scr_compiled_metrics, _ = self.classification_performance_eval(self.output_folder+'robustness/scrambled_' + str(dataset_size) + '_',np.array(scr_compiled_true), np.array(scr_compiled_preds), file_tag = 'compiled')
                    # write results to a text file for the user to read
                    scr_results_file_path = self.write_results(metric_names, scr_avg_metric_folds, scr_std_metric_folds, scr_compiled_metrics, scr_cv_scores, scrambled=True, subset = str(dataset_size))
                
            end3 = time()
            runtime_stat_time = 'Elapsed time for data ablation experiment : ' + str(np.round(((end3 - start3) / 60), 2))  + ' minutes'
            AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')

        # get predictions
        #print(compiled_seqs)
        results_df = pd.DataFrame(np.array([compiled_seqs,np.array(compiled_true)[:,1], np.array(compiled_preds)[:,1]]).T, columns=['Seqs','True','Preds'])
#         results_df = pd.DataFrame(np.array([compiled_seqs,np.array(compiled_true), np.array(compiled_preds)]).T, columns=['Seqs','True','Preds'])
        results_df.to_csv(self.output_folder+'compiled_results_deepswarm_classification.csv')
        
        print('Fitting final model now...')
        # now train final model using all of the data and save for user to run predictions on 
        with suppress_stdout():
            deploy_model = self.fit_final_model(X, y)
        
         # Save the final deploy trained model
        deploy_model.save(self.output_folder + 'deepswarm_deploy_model.h5')
        print_summary(deploy_model, self.output_folder+ 'best_classification_topology.txt')

        final_model_path = self.output_folder
        final_model_name = 'deepswarm_deploy_model.h5'
        numerical = []
        numericalbool = True
        for x in list(df_data_output.values):
            try:
                x = float(x)
                numerical.append(x)
            except Exception as e:
                numericalbool = False
                numerical = list(df_data_output.values.flatten())
                break

        if self.run_interpretation:
            start4 = time()

            # make folder
            if not os.path.isdir(self.output_folder + 'interpretation/'):
                os.mkdir(self.output_folder + 'interpretation/')

            # saliency maps
            print("Generating saliency maps...")
            arr, plot_path, smallalph, seqlen = plot_saliency_maps(numerical_data_input, oh_data_input, alph, final_model_path, final_model_name, self.output_folder + 'interpretation/', '_saliency.png', self.sequence_type, self.interpret_params)
            plot_seqlogos(arr, smallalph, self.sequence_type, plot_path, '_saliency_seq_logo.png', seqlen)

            # class activation maps
            print("Generating class activation maps...")
            arr, plot_path, smallalph, seqlen = plot_activation_maps(numerical_data_input, oh_data_input, alph, final_model_path, final_model_name, self.output_folder + 'interpretation/', '_activation.png', self.sequence_type, self.interpret_params)
            plot_seqlogos(arr, smallalph, self.sequence_type, plot_path, '_activation_seq_logo.png', seqlen)

            # in silico mutagenesis     
            print("Generating in silico mutagenesis plots...")
            with suppress_stdout():
                plot_mutagenesis(numerical_data_input, oh_data_input, alph, numerical, numericalbool, final_model_path, final_model_name, self.output_folder + 'interpretation/', '_mutagenesis.png', self.sequence_type, model_type = 'deepswarm', interpret_params = self.interpret_params)
            
            end4 = time()
            runtime_stat_time = 'Elapsed time for interpretation : ' + str(np.round(((end4 - start4) / 60), 2))  + ' minutes'
            AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')

        if self.run_design:
            start5 = time()
            # make folder
            if not os.path.isdir(self.output_folder + 'design/'):
                os.mkdir(self.output_folder + 'design/')

            print("Generating designed sequences...")
            with suppress_stdout():
                integrated_design(numerical_data_input, oh_data_input, alph, numerical, numericalbool, final_model_path, final_model_name, self.output_folder + 'design/', '_design.png', self.sequence_type, model_type = 'deepswarm', design_params = self.design_params)

            end5 = time()
            runtime_stat_time = 'Elapsed time for design : ' + str(np.round(((end5 - start5) / 60), 2))  + ' minutes'
            AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')

        # metrics are saved in a file (as are plots)
        # return final model
        end = time()
        runtime_stat_time = 'Elapsed time for total : ' + str(np.round(((end - start1) / 60), 2))  + ' minutes'
        AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')
        return deploy_model, [compiled_metrics, avg_metric_folds, std_metric_folds], transform_obj


# In[ ]:


class DeepSwarmRegression(AutoMLRegressor): 
    
    def __init__(self, data_path, model_folder, output_folder, max_runtime, num_folds, sequence_type, do_transform=True, verbosity=0, yaml_params={}, num_final_epochs=50, input_col = 'seq', target_col = 'target', pad_seqs = 'max', augment_data = 'none', dataset_robustness = False, run_interpretation = True, interpret_params = {}, run_design = True, design_params = {}):
        AutoMLRegressor.__init__(self, data_path, model_folder, output_folder, max_runtime, num_folds, do_transform, input_col, target_col)
        self.verbosity = verbosity 
        # pull out yaml parameters and set defaults if not specified
        self.max_depth = yaml_params.get('max_depth', 3) 
        self.ant_count = yaml_params.get('ant_count', 4) 
        self.epochs = yaml_params.get('epochs', 5) 
        self.num_epochs=num_final_epochs
        self.best_model = None 
        self.topology_path = None
        self.pad_seqs = pad_seqs
        self.augment_data = augment_data
        self.sequence_type = sequence_type
        self.dataset_robustness = dataset_robustness
        self.run_interpretation = run_interpretation
        self.interpret_params = interpret_params
        self.run_design = run_design
        self.design_params = design_params
        
    def convert_input(self): 
        return convert_deepswarm_input(self.df_data_input, self.df_data_output, self.pad_seqs, self.augment_data, self.sequence_type)
    
    def update_yaml_file(self, input_shape):
        # file editing
        # help from: https://stackoverflow.com/questions/6866600/how-to-parse-read-a-yaml-file-into-a-python-object 

        settings_folder = './settings/'# + 'default_classification.yaml'
        with open(settings_folder + 'default.yaml') as f:
            data_map = yaml.load(f)

        data_map['DeepSwarm']['metrics']='loss'
        data_map['max_depth'] = self.max_depth
        data_map['DeepSwarm']['aco']['ant_count'] = self.ant_count
        data_map['DeepSwarm']['backend']['epochs'] = self.epochs
        data_map['DeepSwarm']['backend']['loss'] = 'mean_squared_error'
        data_map['DeepSwarm']['max_depth'] = self.max_depth
        data_map['DeepSwarm']['save_folder'] = self.model_folder
        data_map['DeepSwarm']['reuse_patience'] = 1 # made 10 on 03-18-2021 but switched back to 1 on 03-22-2021
        data_map['Nodes']['InputNode']['attributes']['shape']= [input_shape]#[(30,4,1)]
        data_map['Nodes']['OutputNode']['attributes']['output_size']= [1] # for two-class classification 
        data_map['Nodes']['OutputNode']['attributes']['shape']= [1] # for two-class classification 
        data_map['Nodes']['OutputNode']['attributes']['activation']= ['Linear']
        # changing architecture space searched 
        data_map['Nodes']['Conv2DNode']['attributes']['filter_count']= [8,16,32,64]#
        data_map['Nodes']['Conv2DNode']['attributes']['kernel_size']= [1,3,5,7]#
        data_map['Nodes']['DenseNode']['attributes']['output_size']= [30,64,128, 256]#

        with open(settings_folder + 'default.yaml', "w") as f:
            yaml.dump(data_map, f)
            
    def compile_model(self, model):
        optimizer_parameters = {
            'optimizer': 'adam',
            'loss': 'mean_squared_error',
            'metrics': ['mean_squared_error'],
        }

        # If user specified custom optimizer, use it instead of the default one
        model.compile(**optimizer_parameters)
    
    def find_best_architecture(self, X, y):

        from deepswarm.backends import Dataset, TFKerasBackend
        from deepswarm.deepswarm import DeepSwarm

            # override the allowed activations for the backend (include linear for final node of regression)
        def updated_map_activation(self, activation):
            if activation == "ReLU":
                return tf.keras.activations.relu
            if activation == "ELU":
                return tf.keras.activations.elu
            if activation == "LeakyReLU":
                return tf.nn.leaky_relu
            if activation == "Sigmoid":
                return tf.keras.activations.sigmoid
            if activation == "Softmax":
                return tf.keras.activations.softmax
            if activation == 'Linear': 
                return tf.keras.activations.linear
            raise Exception('Not handled activation: %s' % str(activation))
        TFKerasBackend.map_activation = updated_map_activation

        # update to have MSE metric appear during training (for the class defn)
        def updated_compile_model(self, model):
            optimizer_parameters = {
                'optimizer': 'adam',
                'loss': 'mean_squared_error',
                'metrics': ['mean_squared_error'],#['mean_squared_error'],
            }

            # If user specified custom optimizer, use it instead of the default one
            # we also need to deserialize optimizer as it was serialized during init
            if self.optimizer is not None:
                optimizer_parameters['optimizer'] = tf.keras.optimizers.deserialize(self.optimizer)
            model.compile(**optimizer_parameters)
        TFKerasBackend.compile_model = updated_compile_model


        # Ensure replicability (Seed needed here)
        train_size = 0.6
        seed = 7
        np.random.seed(seed)

        if not os.path.isdir(self.model_folder):
            os.makedirs(self.model_folder)

        # DEEPSWARM MODEL FINDING AND TRAINING
        # NOTE: see for example: https://github.com/Pattio/DeepSwarm/blob/master/examples/mnist.py 

        # Prepare Dataset object for Deepswarm
        x_train, x_test, y_train, y_test = train_test_split(X, y, test_size = 1-train_size, random_state = seed)
        x_train = x_train.reshape(x_train.shape[0], x_train.shape[1], x_train.shape[2], 1)
        x_test = x_test.reshape(x_test.shape[0], x_train.shape[1], x_train.shape[2], 1)
        val_split_size = 0.1

        normalized_dataset = Dataset(
            training_examples=x_train,
            training_labels=y_train,
            testing_examples=x_test,
            testing_labels=y_test,
            validation_split=val_split_size,
        )

        #Create backend responsible for training & validating
        backend = TFKerasBackend(dataset=normalized_dataset)
        if self.verbosity>0: print('Deepswarm TFK Backend: Created!')

        # Create DeepSwarm object responsible for optimization
        deepswarm = DeepSwarm(backend=backend)
        if self.verbosity>0: print('Deepswarm Object: Created!')

        # Find the topology for a given dataset
        topology = deepswarm.find_topology()
        if self.verbosity>0: print('Deepswarm Topology: Found!')
        print('test')
        print(topology.summary())
        # Evaluate discovered topology
        deepswarm.evaluate_topology(topology)
        if self.verbosity>0: print('Deepswarm Preliminary Topology Evaluation: Done!')

        # Train topology for N epochs
        base_model = deepswarm.train_topology(topology, self.num_epochs)
        if self.verbosity>0: print('Deepswarm Topology Base Training: Completed!')

        # Evaluate the Base trained model
        deepswarm.evaluate_topology(base_model)
        if self.verbosity>0: print('Deepswarm Topology Evaluation: Completed!')

        # Save the DeepSwarm base trained model using optimal topology
        base_model.save(self.model_folder + 'deepswarm_base_model.h5')

        # Save the DeepSwarm optimal topology with reinitialized weights
        topology = base_model
        
        reset_weights(topology)
        topology.save(self.model_folder + 'deepswarm_topology.h5')

        # Display saving message
        if self.verbosity>0: print('Deepswarm Results: Saved!')
            
        self.best_model = topology 

        return backend, deepswarm, topology, base_model # return destination of model and reload/retrain per fold 
        
    def train_architecture_kfold(self, X, y, transform_obj, seed,alph): 
        
        # run kfold cross-validation over the best model from autokeras
        # model_path is the path of the best pipeline from autokeras

        # set-up k-fold system 
        # default num folds = 10 (can be specified by user)
        kfold = KFold(n_splits=self.num_folds, shuffle=True, random_state=seed)

        # keep track of metrics per fold
        cv_scores = []
        # save predictions
        predictions = [] # in order of folds
        true_targets = [] # in order of folds
        compiled_seqs =[]

        fold_count = 1 # keep track of the fold (when saving models)

        for train, test in kfold.split(X, y):
            print('Current fold:', fold_count)

            # Allocate fold train/test for  exported deepswarm pipeline 
            training_features = np.array(X)[train]
            training_target = y[train]
            testing_features = np.array(X)[test]
            testing_target = y[test]

            # Recreate the exact same model purely from the file
            topology_path = self.model_folder + 'deepswarm_topology.h5'
            self.topology_path = topology_path
            model = tf.keras.models.load_model(topology_path)
            self.compile_model(model)

            # Train topology for N epochs in deepswarm
            validation_spit_init = 0.1
            callbacks_list = [EarlyStopping(monitor='val_loss', patience=math.ceil(self.num_epochs*0.1), verbose = False)]     # Callback to be used for checkpoint generation and early stopping 
            model_history = model.fit(training_features, training_target, validation_split=validation_spit_init, epochs=self.num_epochs, callbacks=callbacks_list)

            # Calculate model predictions for all fold testing samples
            results = model.predict(testing_features)

            # save metrics and key attributes of each fold 
            model_eval_metrics = model.evaluate(testing_features,testing_target)

            # need to inverse transform (if user wanted output to be transformed originally)
            if self.do_transform:
                testing_target_invtransf = transform_obj.inverse_transform(y[test].reshape(-1,1))
                results_invtransf = transform_obj.inverse_transform(results.reshape(-1,1))
                # reverse uniform transformation operation   
                true_targets.extend(testing_target_invtransf)
                predictions.extend(results_invtransf) # keep y_true, y_pred   
            else: 
                true_targets.extend(y[test])
                predictions.extend(results) # keep y_true, y_pred  
            compiled_seqs.extend(AutoMLBackend.reverse_onehot2seq(np.array(X)[test], alph, self.sequence_type, numeric = True))

            # save metrics
            # use function from luis per fold
            if self.do_transform:resulting_metrics = self.regression_performance_eval(self.output_folder+ 'deepswarm_', np.array(testing_target_invtransf), np.array(results_invtransf), str(fold_count))
            else: resulting_metrics = self.regression_performance_eval(self.output_folder+ 'deepswarm_', np.expand_dims(testing_target,1), np.expand_dims(results,1), str(fold_count))
            cv_scores.append(resulting_metrics)
            del model

            fold_count += 1
        return np.array(cv_scores), predictions, true_targets, compiled_seqs

        
    # [Function] Train deploy model using all data
    def fit_final_model(self, X, y): 
        # get best model
        # need to read-in from outputted file and instantiate the pipeline in current cell
        model = fit_final_model(self.topology_path, self.num_epochs, self.compile_model,X, y)
        # can load as follows: model = pickle_from_file(final_model_path)
        self.best_model = model                                                
        return model # final fitted model                                                                    
                                                                
    def run_system(self): 

        start1 = time()
        print('Conducting architecture search now...')
        with suppress_stdout():

            # if user has specified an output folder that doesn't exist, create it 
            if not os.path.isdir(self.output_folder):
                os.makedirs(self.output_folder)

            # transform input
            numerical_data_input, oh_data_input, df_data_output, scrambled_numerical_data_input, scrambled_oh_data_input, alph = self.convert_input()
            # transform output (target) into bins 
            if self.do_transform:
                transformed_output, transform_obj = self.transform_target()
            else: 
                transformed_output = np.array(df_data_output) # don't do any modification to specified target! 
                transform_obj = None

            # now, we have completed the pre-processing needed to feed our data into deepswarm
            # deepswarm input: numerical_data_input
            # deepswarm output: transformed_output
            X = numerical_data_input
            y = transformed_output

            # ensure replicability
            seed = 7
            np.random.seed(seed)
            
            # Update the default.yaml settings file
            self.update_yaml_file(input_shape= (np.shape(X)[1], np.shape(X)[2], 1))

             # run deepswarm to find best model
            backend, deepswarm, topology, base_model  = self.find_best_architecture(X, y,)

            # run kfold cv over the resulting pipeline
            cv_scores, compiled_preds, compiled_true, compiled_seqs = self.train_architecture_kfold(X, y, transform_obj,seed,alph)

            # if failed to execute properly, just stop rest of execution
            if cv_scores is None: return None, None

            # now, get the average scores (find avg r2 and std, to show variability) across folds 
            _, _, avg_r2_folds, _, _ = np.mean(cv_scores, axis = 0) # avg over columns 
            _, _, std_r2_folds, _, _ = np.std(cv_scores, axis = 0) # avg over columns 
            cv_scores = cv_scores.transpose()

            # now get the compiled r2 and generate an overall plot 
            # now get the compiled r2 and generate an overall plot 

            if self.do_transform:
                _, _, compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'deepswarm_', np.array(compiled_true), np.array(compiled_preds), file_tag='compiled')
            else:
                _, _, compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'deepswarm_', np.expand_dims(compiled_true,1), np.expand_dims(compiled_preds,1), file_tag='compiled')

        print('Metrics over folds: \n\tAverage r2: ', avg_r2_folds)
        print('\tStd of r2: ', std_r2_folds)

        if compiled_r2 != avg_r2_folds: 
            #print('Compiled r2 does not match avg over folds. Check for error') # this may be fine- maybe take out this line?
            # this is not a problem with cross validation!
            print('\tOverall r2: ' + str(compiled_r2) + ", Average r2 over folds: " + str(avg_r2_folds))

        # write results to a text file for the user to read
        results_file_path = self.write_results(avg_r2_folds, std_r2_folds, compiled_r2, cv_scores, scrambled=False)
        
        end1 = time()
        runtime_stat_time = 'Elapsed time for autoML search : ' + str(np.round(((end1 - start1) / 60), 2))  + ' minutes'
        AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')
        start2 = time()

        print('Testing scrambled control now...')
        if not os.path.isdir(self.output_folder + 'scrambled/'):
            os.mkdir(self.output_folder + 'scrambled/')

        with suppress_stdout():
            # test scrambled control on best architecture
            scr_X = scrambled_numerical_data_input
            scr_cv_scores, scr_compiled_preds, scr_compiled_true, scr_compiled_seqs = self.train_architecture_kfold(scr_X, y, transform_obj,seed,alph)
            # now, get the average scores (find avg r2 and std, to show variability) across folds 
            _, _, scr_avg_r2_folds, _, _ = np.mean(scr_cv_scores, axis = 0) # avg over columns 
            _, _, scr_std_r2_folds, _, _ = np.std(scr_cv_scores, axis = 0) # avg over columns 
            scr_cv_scores = scr_cv_scores.transpose()

            # now get the compiled metric and generate an overall plot 
            if self.do_transform:
                _, _, scr_compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'scrambled/', np.array(scr_compiled_true), np.array(scr_compiled_preds), file_tag='compiled')
            else:
                _, _, scr_compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'scrambled/', np.expand_dims(scr_compiled_true,1), np.expand_dims(scr_compiled_preds,1), file_tag='compiled')

        print('Scrambled metrics over folds: ')
        print('Metrics over folds: \n\tAverage r2: ', scr_avg_r2_folds)
        print('\tStd of r2: ', scr_std_r2_folds)

        # write results to a text file for the user to read
        scr_results_file_path = self.write_results(scr_avg_r2_folds, scr_std_r2_folds, scr_compiled_r2, scr_cv_scores, scrambled=True)
        
        # get predictions
        results_df = pd.DataFrame(np.array([compiled_seqs,np.array(compiled_true).flatten(), np.array(compiled_preds).flatten()]).T, columns=['Seqs','True','Preds'])
        results_df.to_csv(self.output_folder+'compiled_results_deepswarm_reg.csv')
        
                # write results to a text file for the user to read
        scr_results_file_path = self.write_results(scr_avg_r2_folds, scr_std_r2_folds, scr_compiled_r2, scr_cv_scores, scrambled=True)
        
        end2 = time()
        runtime_stat_time = 'Elapsed time for scrambled control : ' + str(np.round(((end2 - start2) / 60), 2))  + ' minutes'
        AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')

        # dataset robustness test
        if self.dataset_robustness:
            start3 = time()
            dataset_size = len(X)
            if not os.path.isdir(self.output_folder + 'robustness/'):
                os.mkdir(self.output_folder + 'robustness/')
            while dataset_size > 1000:
                dataset_size = int(dataset_size / 2)
                print("Testing with dataset size of: " + str(dataset_size))

                with suppress_stdout():

                    # reshape for deepswarm
                    smallX = X[0:dataset_size].reshape(X[0:dataset_size].shape[0], X[0:dataset_size].shape[1], X[0:dataset_size].shape[2], 1)

                    # run kfold cv over the resulting pipeline
                    cv_scores, compiled_preds, compiled_true,compiled_seqs = self.train_architecture_kfold(smallX, y[0:dataset_size], transform_obj,seed,alph)

                    # now, get the average scores (find avg r2 and std, to show variability) across folds 
                    _, _, avg_r2_folds, _, _ = np.mean(cv_scores, axis = 0) # avg over columns 
                    _, _, std_r2_folds, _, _ = np.std(cv_scores, axis = 0) # avg over columns
                    cv_scores = cv_scores.transpose()

                    # now get the compiled r2 and generate an overall plot 
                    if self.do_transform: 
                        _, _, compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'robustness/' + str(dataset_size) + '_', np.array(compiled_true), np.array(compiled_preds), file_tag='compiled')
                    else:
                        _, _, compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'robustness/' + str(dataset_size) + '_', np.expand_dims(compiled_true,1), np.expand_dims(compiled_preds,1), file_tag='compiled')

                    # write results to a text file for the user to read
                    results_file_path = self.write_results(avg_r2_folds, std_r2_folds, compiled_r2, cv_scores, scrambled = False, subset = str(dataset_size))
                    
                    # test scrambled control on best architecture
                    smallscrX = scr_X[0:dataset_size].reshape(scr_X[0:dataset_size].shape[0], scr_X[0:dataset_size].shape[1], scr_X[0:dataset_size].shape[2], 1)

                    scr_cv_scores, scr_compiled_preds, scr_compiled_true, scr_compiled_seqs = self.train_architecture_kfold(smallscrX, y[0:dataset_size], transform_obj,seed,alph)

                    # now, get the average scores (find avg r2 and std, to show variability) across folds 
                    _, _, scr_avg_r2_folds, _, _ = np.mean(scr_cv_scores, axis = 0) # avg over columns 
                    _, _, scr_std_r2_folds, _, _ = np.std(scr_cv_scores, axis = 0) # avg over columns 
                    scr_cv_scores = scr_cv_scores.transpose()

                    # now get the compiled metric and generate an overall plot 
                    if self.do_transform:
                        _, _, scr_compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'robustness/scrambled_' + str(dataset_size) + '_', np.array(scr_compiled_true), np.array(scr_compiled_preds), file_tag='compiled')
                    else:
                        _, _, scr_compiled_r2, _, _ = self.regression_performance_eval(self.output_folder +'robustness/scrambled_' + str(dataset_size) + '_', np.expand_dims(scr_compiled_true,1), np.expand_dims(scr_compiled_preds,1), file_tag='compiled')

                    # write results to a text file for the user to read
                    scr_results_file_path = self.write_results(scr_avg_r2_folds, scr_std_r2_folds, scr_compiled_r2, scr_cv_scores, scrambled=True, subset = str(dataset_size))
                
            end3 = time()
            runtime_stat_time = 'Elapsed time for data ablation experiment : ' + str(np.round(((end3 - start3) / 60), 2))  + ' minutes'
            AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')

        print('Fitting final model now...')
        # now train final model using all of the data and save for user to run predictions on 
        with suppress_stdout():
            deploy_model = self.fit_final_model(X, y)
        
         # Save the final deploy trained model
        deploy_model.save(self.output_folder + 'deepswarm_deploy_model.h5')
        print_summary(deploy_model, self.output_folder + 'best_regression_topology.txt')

        final_model_path = self.output_folder
        final_model_name = 'deepswarm_deploy_model.h5'
        numerical = []
        numericalbool = True
        for x in list(df_data_output.values):
            try:
                x = float(x)
                numerical.append(x)
            except Exception as e:
                numericalbool = False
                numerical = list(df_data_output.values.flatten())
                break

        if self.run_interpretation:
            start4 = time()
            # make folder
            if not os.path.isdir(self.output_folder + 'interpretation/'):
                os.mkdir(self.output_folder + 'interpretation/')

            # saliency maps
            print("Generating saliency maps...")
            arr, plot_path, smallalph, seqlen = plot_saliency_maps(numerical_data_input, oh_data_input, alph, final_model_path, final_model_name, self.output_folder + 'interpretation/', '_saliency.png', self.sequence_type, self.interpret_params)
            plot_seqlogos(arr, smallalph, self.sequence_type, plot_path, '_saliency_seq_logo.png', seqlen)

            # class activation maps
            print("Generating class activation maps...")
            arr, plot_path, smallalph, seqlen = plot_activation_maps(numerical_data_input, oh_data_input, alph, final_model_path, final_model_name, self.output_folder + 'interpretation/', '_activation.png', self.sequence_type, self.interpret_params)
            plot_seqlogos(arr, smallalph, self.sequence_type, plot_path, '_activation_seq_logo.png', seqlen)

            # in silico mutagenesis     
            print("Generating in silico mutagenesis plots...")
            with suppress_stdout():
                plot_mutagenesis(numerical_data_input, oh_data_input, alph, numerical, numericalbool, final_model_path, final_model_name, self.output_folder + 'interpretation/', '_mutagenesis.png', self.sequence_type, model_type = 'deepswarm', interpret_params = self.interpret_params)
            
            end4 = time()
            runtime_stat_time = 'Elapsed time for interpretation : ' + str(np.round(((end4 - start4) / 60), 2))  + ' minutes'
            AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')
        
        if self.run_design:
            start5 = time()
            
            # make folder
            if not os.path.isdir(self.output_folder + 'design/'):
                os.mkdir(self.output_folder + 'design/')

            print("Generating designed sequences...")
            # class_of_interest must be zero for regression
            with suppress_stdout():
                integrated_design(numerical_data_input, oh_data_input, alph, numerical, numericalbool, final_model_path, final_model_name, self.output_folder + 'design/', '_design.png', self.sequence_type, model_type = 'deepswarm', design_params = self.design_params)

            end5 = time()
            runtime_stat_time = 'Elapsed time for design : ' + str(np.round(((end5 - start5) / 60), 2))  + ' minutes'
            AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')

        # metrics are saved in a file (as are plots)
        # return final model
        end = time()
        runtime_stat_time = 'Elapsed time for total : ' + str(np.round(((end - start1) / 60), 2))  + ' minutes'
        AutoMLBackend.print_stats([runtime_stat_time], self.output_folder+ 'runtime_statistics.txt')
        return deploy_model, [compiled_r2, avg_r2_folds, std_r2_folds], transform_obj



