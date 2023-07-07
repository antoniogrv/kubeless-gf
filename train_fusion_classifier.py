from typing import Optional
from typing import Final
from typing import Dict

from dotenv import load_dotenv
import numpy as np
import logging
import torch
import os

from tokenizer import MyDNATokenizer
from tokenizer import DNABertTokenizer

from dataset import FusionDatasetConfig
from dataset import FusionDataset
from torch.utils.data import DataLoader

from model import MyModelConfig
from model import MyModel
from model import evaluate_weights

from model import FCFullyConnectedModelConfig
from model import FCFullyConnected

from model import FCRecurrentNNConfig
from model import FCRecurrentNN

from torch.optim import AdamW

from train_gene_classifier import train_gene_classifier

from utils import SEPARATOR
from utils import define_fusion_classifier_inputs
from utils import create_test_id
from utils import init_test
from utils import setup_logger
from utils import log_results
from utils import save_result
from utils import close_loggers


def train_fusion_classifier(
        len_read: int,
        len_kmer: int,
        n_words: int,
        tokenizer_selected: str,
        n_fusion: int,
        gc_model_selected: str,
        gc_hyperparameters: Dict[str, any],
        gc_batch_size: int,
        gc_re_train: bool,
        model_selected: str,
        fc_hyper_parameters: Dict[str, any],
        batch_size: int,
        freeze: bool,
        re_train: bool,
        grid_search: bool,
):
    # execute train_gene_classifier
    gc_model_path, gc_model_config = train_gene_classifier(
        len_read=len_read,
        len_kmer=len_kmer,
        n_words=n_words,
        tokenizer_selected=tokenizer_selected,
        model_selected=gc_model_selected,
        hyper_parameters=gc_hyperparameters,
        batch_size=gc_batch_size,
        re_train=gc_re_train,
        grid_search=True
    )

    # get value from .env
    root_dir: Final = os.getenv('ROOT_LOCAL_DIR')

    # init tokenizer
    tokenizer: Optional[MyDNATokenizer] = None
    if tokenizer_selected == 'dna_bert':
        tokenizer = DNABertTokenizer(
            root_dir=root_dir,
            len_kmer=len_kmer,
            add_n=False
        )
    elif tokenizer_selected == 'dna_bert_n':
        tokenizer = DNABertTokenizer(
            root_dir=root_dir,
            len_kmer=len_kmer,
            add_n=True
        )

    # init configuration
    n_kmers: int = (len_read - len_kmer) + 1
    n_sentences: int = (n_kmers - n_words) + 1
    model_config: Optional[MyModelConfig] = None
    if model_selected == 'fc':
        model_config = FCFullyConnectedModelConfig(
            gene_classifier_name=gc_model_selected,
            gene_classifier_path=gc_model_path,
            n_sentences=n_sentences,
            freeze=freeze,
            **fc_hyper_parameters
        )
    elif model_selected == 'rnn':
        model_config = FCRecurrentNNConfig(
            gene_classifier_name=gc_model_selected,
            gene_classifier_path=gc_model_path,
            n_sentences=n_sentences,
            freeze=freeze,
            **fc_hyper_parameters
        )

    # generate test id
    test_id: str = create_test_id(
        len_read=len_read,
        len_kmer=len_kmer,
        n_words=n_words,
        tokenizer=tokenizer,
        gc_config=gc_model_config,
        fc_config=model_config
    )

    # create dataset configuration
    dataset_conf: FusionDatasetConfig = FusionDatasetConfig(
        genes_panel_path=os.getenv('GENES_PANEL_LOCAL_PATH'),
        len_read=len_read,
        len_kmer=len_kmer,
        n_words=n_words,
        tokenizer=tokenizer,
        n_fusion=n_fusion,
    )

    # get global variables
    task: str = os.getenv('FUSION_CLASSIFIER_TASK')
    result_dir: str = os.path.join(os.getcwd(), os.getenv('RESULTS_LOCAL_DIR'))
    model_name: str = os.getenv('MODEL_NAME')

    # init test
    parent_dir, test_dir, log_dir, model_dir, model_path = init_test(
        result_dir=result_dir,
        task=task,
        model_selected=model_selected,
        test_id=test_id,
        model_name=model_name,
        re_train=re_train
    )

    # if the model has not yet been trained
    if not os.path.exists(model_path):
        # init loggers
        logger: logging.Logger = setup_logger(
            'logger',
            os.path.join(log_dir, 'logger.log')
        )
        train_logger: logging.Logger = setup_logger(
            'train',
            os.path.join(log_dir, 'train.log')
        )

        # load train and validation dataset
        train_dataset = FusionDataset(
            root_dir=root_dir,
            conf=dataset_conf,
            dataset_type='train'
        )
        val_dataset = FusionDataset(
            root_dir=root_dir,
            conf=dataset_conf,
            dataset_type='val'
        )

        # log information
        logger.info(f'Read len: {len_read}')
        logger.info(f'Kmers len: {len_kmer}')
        logger.info(f'Words inside a sentence: {n_words}')
        logger.info(f'Tokenizer used: {tokenizer_selected}')
        logger.info(f'No. fusions generated for each gene: {n_fusion}')
        logger.info(SEPARATOR)
        logger.info(f'Number of train sentences: {len(train_dataset)}')
        logger.info(f'Number of val sentences: {len(val_dataset)}')
        logger.info(f'Number of class: {train_dataset.classes()}')
        logger.info(f'Batch size: {batch_size}')
        logger.info(SEPARATOR)
        logger.info(f'Number of train sentences: {len(train_dataset)}')
        logger.info(f'Number of val sentences: {len(val_dataset)}')
        logger.info(f'Number of class: {train_dataset.classes()}')
        logger.info(f'Batch size: {batch_size}')
        logger.info(SEPARATOR)
        # print dataset status
        logger.info('No. records train set')
        logger.info(train_dataset.print_dataset_status())
        logger.info('No. records val set')
        logger.info(val_dataset.print_dataset_status())

        # load train and validation dataloader
        train_loader: DataLoader = DataLoader(
            train_dataset,
            batch_size=batch_size,
            shuffle=True
        )
        val_loader: DataLoader = DataLoader(
            val_dataset,
            batch_size=batch_size,
            shuffle=True
        )

        # set device gpu if cuda is available
        device: torch.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        # evaluating weights for criterion function
        y_true = []
        for idx, label in enumerate(train_dataset.get_dataset_status()):
            y_true = np.append(y_true, [idx] * label)
        class_weights: torch.Tensor = evaluate_weights(
            y_true=y_true,
            binary=train_dataset.classes() == 2
        ).to(device)

        # define model
        model: Optional[MyModel] = None
        if model_selected == 'fc':
            model: MyModel = FCFullyConnected(
                model_dir=model_dir,
                model_name=model_name,
                config=model_config,
                n_classes=train_dataset.classes(),
                weights=class_weights
            )
        elif model_selected == 'rnn':
            model: MyModel = FCRecurrentNN(
                model_dir=model_dir,
                model_name=model_name,
                config=model_config,
                n_classes=train_dataset.classes(),
                weights=class_weights
            )

        # log model hyper parameters
        logger.info('Model hyper parameters')
        logger.info(model_config)

        # init optimizer
        optimizer = AdamW(
            model.parameters(),
            lr=5e-5,
            eps=1e-8,
            betas=(0.9, 0.999)
        )

        # put model on device available
        model.to(device)
        # train it
        model.train_model(
            train_loader=train_loader,
            optimizer=optimizer,
            device=device,
            epochs=1000,
            evaluation=True,
            val_loader=val_loader,
            logger=train_logger
        )

        # close loggers
        close_loggers([train_logger, logger])
        del train_logger
        del logger

    # if the model is already trained and the grid search parameter is set to true then stop
    elif grid_search:
        return


if __name__ == '__main__':
    # define inputs for this script
    __args, __gc_hyperparameters, __hyperparameters = define_fusion_classifier_inputs()

    # load dotenv file
    load_dotenv(dotenv_path=os.path.join(os.getcwd(), '.env'))

    # execute train_gene_classifier method
    train_fusion_classifier(
        **__args,
        gc_hyperparameters=__gc_hyperparameters,
        fc_hyper_parameters=__hyperparameters
    )
