import os
import sys

import shutil
import tensorflow as tf
import pandas as pd
import numpy as np
import logging
import datetime
import keras
import pickle

import attention_estimator_model
import utility


'''
Hyperparameter configuration section
'''
CLASSES = {'1.0': 1, '2.0': 0}
EVAL_INTERVAL = 50
# These values will come from command line argument
MAX_SEQUENCE_LENGTH = None
VOCAB_SIZE = None
EMBEDDING_DIM = None


def input_function(texts, labels, tokenizer, batch_size, mode):
    # Transform sentence to sequence of integers
    sentence_token = tokenizer.texts_to_sequences(texts)

    # Pad the sequence to max value. 
    sentence_token = tf.keras.preprocessing.sequence.pad_sequences(sentence_token, maxlen=MAX_SEQUENCE_LENGTH)


    if mode == tf.estimator.ModeKeys.TRAIN:
        # loop indefinitely
        num_epochs = None 
        shuffle = True
    else:
        num_epochs = 1
        shuffle = False

    return tf.compat.v1.estimator.inputs.numpy_input_fn(
        x=sentence_token, y=labels, batch_size=batch_size, num_epochs=num_epochs, shuffle=shuffle
    )


def serving_input_fn():
    # Defines the features to be passed to the model during inference 
    # Expects already tokenized and padded representation of sentences

    feature_placeholder = tf.placeholder(tf.float32, [None, MAX_SEQUENCE_LENGTH])
    features = feature_placeholder
    return tf.estimator.export.TensorServingInputReceiver(features, feature_placeholder)

def attention_estimator(model_version, model_dir, config, learning_rate, grad_clip_rate, rnn_units, embedding_dim, dropout_rate, word_index=None, embedding_path=None):

    input_dim = min(len(word_index)+1, VOCAB_SIZE)


    if embedding_path != None:
        matrix = utility.get_embedding(embedding_path)
        embedding_matrix = utility.get_sentence_level_embedding(word_index, matrix, embedding_dim, VOCAB_SIZE)
    else:
        embedding_matrix = None
    
    if model_version == 'lstm_attention':
        attention_model = attention_estimator_model.attention_LSTM_model(input_dim, MAX_SEQUENCE_LENGTH, learning_rate, embedding_dim, rnn_units, dropout_rate,
                                embedding=embedding_matrix, word_index=word_index)
    elif model_version == 'gru_attention':
        attention_model = attention_estimator_model.attention_GRU_model(input_dim, MAX_SEQUENCE_LENGTH, learning_rate, embedding_dim, rnn_units, dropout_rate,
                                embedding=embedding_matrix, word_index=word_index)
    else:
        print("Incorrect model version specified")
        return 

    print(attention_model.summary())
    metrics_list = ['acc', tf.keras.metrics.AUC(), tf.keras.metrics.Precision(), tf.keras.metrics.Recall()]

    adamOptimizer = tf.keras.optimizers.Adam(lr=learning_rate)
    attention_model.compile(optimizer=adamOptimizer, loss='binary_crossentropy', clipnorm=grad_clip_rate, metrics=metrics_list)
    estimator = tf.keras.estimator.model_to_estimator(keras_model=attention_model, model_dir=model_dir, config=config)
    return estimator

def train_and_evaluate(output_dir, hparams):
    # Main orchastrator of training and evaluation by calling models from estimator_model.py
    shutil.rmtree(output_dir, ignore_errors = True)
    tf.compat.v1.summary.FileWriterCache.clear()

    # Set log configuration, export to local file
    date_string = datetime.datetime.now().strftime("%m%d_%H%M")
    filename = 'training log/train_estimator_log_' + date_string + '.txt'
    logging.basicConfig(filename=filename, level=20)

    ((train_text, train_label), (eval_text, eval_label)) = utility.load_data("warehouse/store/", CLASSES)


    # Create vocabulary from training corpus 
    tokenizer = tf.keras.preprocessing.text.Tokenizer(num_words=VOCAB_SIZE+1, oov_token='<unk>')
    tokenizer.fit_on_texts(train_text)

    # Save token dictionary to use during prediction time
    # saving
    with open('tokenizer.pickle', 'wb') as handle:
        pickle.dump(tokenizer, handle, protocol=pickle.HIGHEST_PROTOCOL)

    # Create estimator config
    run_config = tf.estimator.RunConfig(save_checkpoints_steps=EVAL_INTERVAL,
                                        log_step_count_steps = 20,
                                        save_summary_steps = 50
                                    )
    estimator = attention_estimator(hparams['model_version'], output_dir, run_config,
                                hparams['learning_rate'],
                                hparams['grad_clip_rate'],
                                hparams['rnn_units'],
                                EMBEDDING_DIM,
                                hparams['dropout_rate'],
                                word_index=tokenizer.word_index,
                                embedding_path=hparams['embedding_path'])

    train_steps = hparams['num_epochs'] * len(train_text) / hparams['batch_size']
    train_spec = tf.estimator.TrainSpec(input_fn=input_function(train_text, train_label, tokenizer,
            hparams['batch_size'], mode=tf.estimator.ModeKeys.TRAIN), max_steps=train_steps)

    # Create exporter configuration
    exporter = tf.estimator.LatestExporter('exporter', serving_input_fn)
    eval_spec = tf.estimator.EvalSpec(input_fn=input_function(eval_text, eval_label, tokenizer,
            hparams['batch_size'], mode=tf.estimator.ModeKeys.EVAL), 
            steps=None, 
            exporters=exporter,
            start_delay_secs=50,
            throttle_secs = EVAL_INTERVAL)  # evaluate every N seconds

    tf.estimator.train_and_evaluate(estimator, train_spec, eval_spec)
    return True