from glob import glob
import os


def create_folders(task: str, model_name: str, parent_name: str):
    # create log folder
    log_path = os.path.join(os.getcwd(), 'log', task, model_name, parent_name)
    if not os.path.exists(log_path):
        os.makedirs(log_path)
    # create model folder
    model_path = os.path.join(log_path, 'model')
    if not os.path.exists(model_path):
        os.makedirs(model_path)

    return log_path, model_path


def delete_checkpoints(model_path: str):
    for model_checkpoint in glob(os.path.join(model_path, 'model_*.h5')):
        os.remove(model_checkpoint)