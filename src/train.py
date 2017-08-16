#!/usr/bin/env python
__doc__ = """

Training.

Nicholas Turner <nturner@cs.princeton.edu>, 2017
"""

import numpy as np
import os
import time


import torch
from torch import autograd


import train_utils as utils


required_params = ["max_iter","test_intv","test_iter",
                   "avgs_intv","chkpt_intv","expt_dir"]


def train(model, loss_fn, optimizer, sampler, val_sampler=None, last_iter=0, **params):
    """ Generalized training fn """

    assert params_defined(params), "Params under-specified"

    #Start stats log
    monitor = utils.LearningMonitor()

    #Determine the names of inputs, labels, masks
    sample_spec = utils.SampleSpec(sampler.get().keys())
    mask_names = sample_spec.get_masks()

    start = time.time()
    print("======= BEGIN TRAINING LOOP ========")
    for i in range(last_iter, params['max_iter']):

        #Make sure no mask is empty (data for all tasks)
        sample = fetch_nonempty_sample(sampler, mask_names)

        inputs, labels, masks = make_variables(sample, sample_spec, "train")

        #Running forward pass
        preds = model(*inputs)

        losses, nmsks = eval_error(preds, labels, masks, loss_fn, sample_spec)

        update_model(optimizer, losses)

        log_errors(monitor, losses, nmsks)

        # Elapsed time.
        elapsed = time.time() - start
        log_elapsed_time(monitor, elapsed, "train")
        start = time.time()

        if val_sampler is not None and i % params["test_intv"] == 0:
            run_validation(model, val_sampler, params["test_iter"],
                           loss_fn, sample_spec, monitor, i)
            start = time.time() #ignore validation time

        if i % params["avgs_intv"] == 0 and i != last_iter:
            monitor.compute_avgs(i, "train")

            #Displaying stats
            avg_losses = { k : round(monitor.get_last_value(k, "train"),5)
                           for k in losses.keys() }
            avg_time = round(monitor.get_last_value("iter_time","train"),5)
            print("iter: {}; avg losses = {} (iter_time = {} s on avg)".format(i,avg_losses, avg_time))

        if i % params["chkpt_intv"] == 0 and i != last_iter:
            print("SAVE CHECKPOINT: {} iters.".format(i))
            save_checkpoint(model, monitor, i, params["expt_dir"])



def log_elapsed_time(monitor, elapsed_time, phase):
    """ Stores the iteration time within the LearningMonitor """
    monitor.add_to_num({"iter_time":elapsed_time}, phase)
    monitor.add_to_denom({"iter_time":1}, phase)


def log_errors(monitor, losses, nmsks, phase="train"):
    """ Adds the losses to the running averages within the LearningMonitor """

    assert losses.keys() == nmsks.keys(), "Mismatched losses and nmsks"

    #Extracting values from Tensors
    losses = { k : v.data[0] for (k,v) in losses.items() }
    nmsks  = { k : v.data[0] for (k,v) in nmsks.items()  }

    monitor.add_to_num(losses, phase)
    monitor.add_to_denom(nmsks, phase)


def update_model(optimizer, losses):
    """ Runs the backward pass and updates model parameters """
    optimizer.zero_grad()
    total_loss = sum(losses.values())
    total_loss.backward()
    optimizer.step()


def eval_error(preds, labels, masks, loss_fn, sample_spec):
    """
    Evaluates the error of the predictions according to the available
    labels and masks

    Assumes labels are ordered according to the sample_spec
    """

    label_names = sample_spec.get_labels()

    assert len(label_names) == len(labels), "Mismatched labels and label names"
    assert len(preds) == len(labels), "Mismatched preds and labels"

    losses = dict(); nmsks = dict();

    for (i,pred) in enumerate(preds):

        label = labels[i]
        label_name = label_names[i]

        if sample_spec.has_mask( label_name ):
            mask = masks[sample_spec.get_mask_index(label_name)]

            losses[label_name] = loss_fn(pred, label, mask)
            nmsks[label_name]  = mask.sum()

        else:
            losses[label_name] = loss_fn(pred, label)
            #Wrapping the value in a torch Variable to give a
            # uniform interface (particularly for the log_errors fn)
            nmsks[label_name]  = autograd.Variable(torch.Tensor(
                                    [np.prod(label.size())]))

    return losses, nmsks


def save_checkpoint(model, monitor, i, base_dir):
    """ Writes model checkpoint and matching learning curve to disk """
    # Save model
    ckpt_fname = os.path.join(base_dir, "models", "model{}.ckpt".format(i))
    torch.save(model.state_dict(), ckpt_fname)

    # Save stats
    stats_fname = os.path.join(base_dir, "logs", "stats_{}.h5".format(i))
    monitor.save(stats_fname, i)

#=======================================
# Helper Fns
#=======================================

def params_defined(params):
    " Checks whether all required parameters have been defined "

    defined_keys = set(params.keys())
    for param in required_params:
      if not param in defined_keys:
        return False

    return True


def fetch_nonempty_sample(sampler, masks):
    """
    Pulls a sample from the sampler with SOME unmasked
    voxels for each task
    """

    sample = sampler.get()

    #Making sure no masks are empty
    while utils.masks_not_empty(sample, masks):
      sample = sampler.get()

    # Reshape to add sample dimension (minibatch size = 1).
    for k, v in sample.iteritems():
        sample[k] = np.expand_dims(v, axis=0)

    return sample


def make_variables(sample, sample_spec, phase="train"):
    """ Creates the Torch variables for a sample """

    inputs = sample_spec.get_inputs()
    labels = sample_spec.get_labels()
    masks  = sample_spec.get_masks()

    if phase == "train":
        input_vars = [utils.make_variable(sample[k], True)  for k in inputs]
    elif phase == "test":
        input_vars = [utils.make_variable(sample[k], volatile=True)  for k in inputs]

    label_vars = [utils.make_variable(sample[k], False) for k in labels]
    mask_vars  = [utils.make_variable(sample[k], False) for k in masks]

    return input_vars, label_vars, mask_vars


def run_validation(model, sampler, num_iters, loss_fn, sample_spec, monitor, iter_num):

    mask_names = sample_spec.get_masks()
    start = time.time()
    for i in range(num_iters):

        #Make sure no mask is empty (data for all tasks)
        sample = fetch_nonempty_sample(sampler, mask_names)

        inputs, labels, masks = make_variables(sample, sample_spec, "test")

        #Running forward pass
        preds = model(*inputs)

        losses, nmsks = eval_error(preds, labels, masks, loss_fn, sample_spec)

        log_errors(monitor, losses, nmsks, "test")

        # Elapsed time.
        elapsed = time.time() - start
        log_elapsed_time(monitor, elapsed, "test")
        start = time.time()

    monitor.compute_avgs(iter_num, "test")
    avg_losses = { k : round(monitor.get_last_value(k, "test"),5) for k in losses.keys() }
    avg_time = round(monitor.get_last_value("iter_time","test"),5)

    print("TEST: {} avg losses = {} (elapsed = {} s avg)".format(iter_num, avg_losses, avg_time))