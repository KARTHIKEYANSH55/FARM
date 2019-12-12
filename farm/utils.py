import logging
import random

import numpy as np
import torch
from torch import multiprocessing as mp
import mlflow
from copy import deepcopy
from farm.visual.ascii.images import WELCOME_BARN, WORKER_M, WORKER_F, WORKER_X


logger = logging.getLogger(__name__)




def set_all_seeds(seed, n_gpu=0):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if n_gpu > 0:
        torch.cuda.manual_seed_all(seed)

def calc_chunksize(num_dicts, min_chunksize=4, max_chunksize=2000):
    num_cpus = mp.cpu_count() or 1
    dicts_per_cpu = np.ceil(num_dicts / num_cpus)
    # automatic adjustment of multiprocessing chunksize
    # for small files (containing few dicts) we want small chunksize to ulitize all available cores but never less
    # than 2, because we need it to sample another random sentence in LM finetuning
    # for large files we want to minimize processor spawning without giving too much data to one process, so we
    # clip it at 5k
    multiprocessing_chunk_size = int(np.clip((np.ceil(dicts_per_cpu / 5)), a_min=min_chunksize, a_max=max_chunksize))
    # This lets us avoid cases in lm_finetuning where a chunk only has a single doc and hence cannot pick
    # a valid next sentence substitute from another document
    while num_dicts % multiprocessing_chunk_size == 1:
        multiprocessing_chunk_size -= -1
    dict_batches_to_process = int(num_dicts / multiprocessing_chunk_size)
    num_cpus_used = min(mp.cpu_count(), dict_batches_to_process) or 1
    return multiprocessing_chunk_size,num_cpus_used


def initialize_device_settings(use_cuda, local_rank=-1, fp16=False):
    if not use_cuda:
        device = torch.device("cpu")
        n_gpu = 0
    elif local_rank == -1:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        if not torch.cuda.is_available():
            n_gpu = 0
        else:
            n_gpu = torch.cuda.device_count()
    else:
        torch.cuda.set_device(local_rank)
        device = torch.device("cuda", local_rank)
        n_gpu = 1
        # Initializes the distributed backend which will take care of sychronizing nodes/GPUs
        torch.distributed.init_process_group(backend="nccl")
    logger.info(
        "device: {} n_gpu: {}, distributed training: {}, 16-bits training: {}".format(
            device, n_gpu, bool(local_rank != -1), fp16
        )
    )
    return device, n_gpu


class BaseMLLogger:
    """
    Base class for tracking experiments.

    This class can be extended to implement custom logging backends like MLFlow, Tensorboard, or Sacred.
    """

    def __init__(self, tracking_uri, **kwargs):
        self.tracking_uri = tracking_uri
        print(WELCOME_BARN)

    def init_experiment(self, tracking_uri):
        raise NotImplementedError()

    @classmethod
    def log_metrics(cls, metrics, step):
        raise NotImplementedError()

    @classmethod
    def log_artifacts(cls, self):
        raise NotImplementedError()

    @classmethod
    def log_params(cls, params):
        raise NotImplementedError()


class MLFlowLogger(BaseMLLogger):
    """
    Logger for MLFlow experiment tracking.
    """

    def init_experiment(self, experiment_name, run_name=None, nested=True):
        mlflow.set_tracking_uri(self.tracking_uri)
        mlflow.set_experiment(experiment_name)
        mlflow.start_run(run_name=run_name, nested=nested)

    @classmethod
    def log_metrics(cls, metrics, step):
        mlflow.log_metrics(metrics, step=step)

    @classmethod
    def log_params(cls, params):
        mlflow.log_params(params)

    @classmethod
    def log_artifacts(cls, dir_path, artifact_path=None):
        mlflow.log_artifacts(dir_path, artifact_path)


class TensorBoardLogger(BaseMLLogger):
    """
    PyTorch TensorBoard Logger
    """

    def __init__(self, **kwargs):
        from tensorboardX import SummaryWriter
        TensorBoardLogger.summary_writer = SummaryWriter()
        super().__init__(**kwargs)

    @classmethod
    def log_metrics(cls, metrics, step):
        for key, value in metrics.items():
            TensorBoardLogger.summary_writer.add_scalar(
                tag=key, scalar_value=value, global_step=step
            )

    @classmethod
    def log_params(cls, params):
        for key, value in params.items():
            TensorBoardLogger.summary_writer.add_text(tag=key, text_string=str(value))


def to_numpy(container):
    try:
        return container.cpu().numpy()
    except AttributeError:
        return container


def convert_iob_to_simple_tags(preds, spans):
    simple_tags = []
    merged_spans = []
    open_tag = False
    for pred, span in zip(preds, spans):
        # no entity
        if not ("B-" in pred or "I-" in pred):
            if open_tag:
                # end of one tag
                merged_spans.append(cur_span)
                simple_tags.append(cur_tag)
                open_tag = False
            continue

        # new span starting
        elif "B-" in pred:
            if open_tag:
                # end of one tag
                merged_spans.append(cur_span)
                simple_tags.append(cur_tag)
            cur_tag = pred.replace("B-", "")
            cur_span = span
            open_tag = True

        elif "I-" in pred:
            this_tag = pred.replace("I-", "")
            if open_tag and this_tag == cur_tag:
                cur_span["end"] = span["end"]
            elif open_tag:
                # end of one tag
                merged_spans.append(cur_span)
                simple_tags.append(cur_tag)
                open_tag = False
    if open_tag:
        merged_spans.append(cur_span)
        simple_tags.append(cur_tag)
        open_tag = False
    return simple_tags, merged_spans


def flatten_list(nested_list):
    """Flatten an arbitrarily nested list, without recursion (to avoid
    stack overflows). Returns a new list, the original list is unchanged.
    >> list(flatten_list([1, 2, 3, [4], [], [[[[[[[[[5]]]]]]]]]]))
    [1, 2, 3, 4, 5]
    >> list(flatten_list([[1, 2], 3]))
    [1, 2, 3]
    """
    nested_list = deepcopy(nested_list)

    while nested_list:
        sublist = nested_list.pop(0)

        if isinstance(sublist, list):
            nested_list = sublist + nested_list
        else:
            yield sublist

def log_ascii_workers(n, logger):
    m_worker_lines = WORKER_M.split("\n")
    f_worker_lines = WORKER_F.split("\n")
    x_worker_lines = WORKER_X.split("\n")
    all_worker_lines = []
    for i in range(n):
        rand = np.random.randint(low=0,high=3)
        if(rand % 3 == 0):
            all_worker_lines.append(f_worker_lines)
        elif(rand % 3 == 1):
            all_worker_lines.append(m_worker_lines)
        else:
            all_worker_lines.append(x_worker_lines)
    zipped = zip(*all_worker_lines)
    for z in zipped:
        logger.info("  ".join(z))

def format_log(ascii, logger):
    ascii_lines = ascii.split("\n")
    for l in ascii_lines:
        logger.info(l)

def encode_id(id):
    """ Convert a 24 or 32, 40 digit hexadecimal id to 4 ints. The first int is the length of the original id.
    The remaining 3 ints are the base 10 equivalents of one third of the hexadecimal.
    This is needed since PyTorch tensors cannot be created for ints bigger than 64 bit"""
    if id is None:
        return 0, 0, 0, 0
    len_id = len(id)
    assert len_id in [24, 32, 40]
    if len_id == 40:
        hexa_1 = id[:14]
        hexa_2 = id[14:27]
        hexa_3 = id[27:]
    else:
        third = int(len_id / 3)
        hexa_1 = id[:third]
        hexa_2 = id[third:third*2]
        hexa_3 = id[third*2:]
    int_1 = int(hexa_1, 16)
    int_2 = int(hexa_2, 16)
    int_3 = int(hexa_3, 16)
    return len_id, int_1, int_2, int_3

def decode_id(len_id, int_1, int_2, int_3):
    hexa_1 = hex(int_1)[2:]
    hexa_2 = hex(int_2)[2:]
    hexa_3 = hex(int_3)[2:]
    if len_id == 40:
        hexa_1 = hexa_1.zfill(14)
        hexa_2 = hexa_2.zfill(13)
        hexa_3 = hexa_3.zfill(13)
    else:
        third = int(len_id / 3)
        hexa_1 = hexa_1.zfill(third)
        hexa_2 = hexa_2.zfill(third)
        hexa_3 = hexa_3.zfill(third)
    hexa = hexa_1 + hexa_2 + hexa_3
    assert len(hexa) == len_id
    return hexa
