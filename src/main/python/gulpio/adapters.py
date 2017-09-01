#!/usr/bin/env python
import random
import os
import json
import csv
import gzip
import glob
from abc import ABC, abstractmethod


from .utils import (get_single_video_path,
                    find_images_in_folder,
                    resize_images,
                    resize_by_short_edge,
                    burst_video_into_frames,
                    temp_dir_for_bursting,
                    ImageNotFound,
                    )


class AbstractDatasetAdapter(ABC):  # pragma: no cover
    """ Base class adapter for gulping (video) datasets.

    Inherit from this class and implement the `iter_data` method. This method
    should iterate over your entire dataset and for each element return a
    dictionary with the following fields:

        id     : a unique(?) ID for the element.
        frames : a list of frames (PIL images, numpy arrays..)
        meta   : a dictionary with arbitrary metadata (labels, start_time...)

    For examples, see the custom adapters below.

    """

    @abstractmethod
    def iter_data(self, slice_element=None):
        return NotImplementedError

    @abstractmethod
    def __len__(self):
        return NotImplementedError


class Custom20BNAdapterMixin(object):

    def create_label2idx_dict(self, label_name):
        labels = sorted(set([item[label_name] for item in self.data]))
        labels2idx = {}
        for label_counter, label in enumerate(labels):
            labels2idx[label] = label_counter
        return labels2idx

    def write_label2idx_dict(self):
        json.dump(self.labels2idx,
                  open(os.path.join(self.output_folder, 'label2idx.json'),
                       'w'))


class Custom20BNJsonVideoAdapter(AbstractDatasetAdapter,
                                 Custom20BNAdapterMixin):
    """ Adapter for 20BN datasets specified by JSON file and MP4 videos. """

    def __init__(self, json_file, folder, output_folder,
                 shuffle=False, frame_size=-1, frame_rate=8,
                 shm_dir_path='/dev/shm'):
        self.json_file = json_file
        if json_file.endswith('.json.gz'):
            self.data = self.read_gz_json(json_file)
        elif json_file.endswith('.json'):
            self.data = self.read_json(json_file)
        else:
            raise RuntimeError('Wrong data file format (.json.gz or .json)')
        self.output_folder = output_folder
        self.labels2idx = self.create_label2idx_dict('template')
        self.folder = folder
        self.shuffle = bool(shuffle)
        self.frame_size = int(frame_size)
        self.frame_rate = int(frame_rate)
        self.shm_dir_path = shm_dir_path
        self.all_meta = self.get_meta()
        if self.shuffle:
            random.shuffle(self.all_meta)

    def read_json(self, json_file):
        with open(json_file, 'r') as f:
            content = json.load(f)
        return content

    def read_gz_json(self, gz_json_file):
        with gzip.open(gz_json_file, 'rt') as fp:
            content = json.load(fp)
        return content

    def get_meta(self):
        return [{'id': entry['id'],
                 'label': entry['template'],
                 'idx': self.labels2idx[entry['template']]}
                for entry in self.data]

    def __len__(self):
        return len(self.data)

    def iter_data(self, slice_element=None):
        slice_element = slice_element or slice(0, len(self))
        for meta in self.all_meta[slice_element]:
            video_folder = os.path.join(self.folder, str(meta['id']))
            video_path = get_single_video_path(video_folder, format_='mp4')
            with temp_dir_for_bursting(self.shm_dir_path) as temp_burst_dir:
                frame_paths = burst_video_into_frames(
                    video_path, temp_burst_dir, frame_rate=self.frame_rate)
                frames = list(resize_images(frame_paths, self.frame_size))
            result = {'meta': meta,
                      'frames': frames,
                      'id': meta['id']}
            yield result
        else:
            self.write_label2idx_dict()


class Custom20BNCsvJpegAdapter(AbstractDatasetAdapter,
                               Custom20BNAdapterMixin):
    """ Adapter for 20BN datasets specified by CSV file and JPEG frames. """

    def __init__(self, csv_file, folder, output_folder,
                 shuffle=False, frame_size=-1, shm_dir_path='/dev/shm'):
        self.data = self.read_csv(csv_file)
        self.output_folder = output_folder
        self.labels2idx = self.create_label2idx_dict('label')
        self.folder = folder
        self.shuffle = shuffle
        self.frame_size = frame_size
        self.shm_dir_path = shm_dir_path
        self.all_meta = self.get_meta()
        if self.shuffle:
            random.shuffle(self.all_meta)

    def read_csv(self, csv_file):
        with open(csv_file, newline='\n') as f:
            content = csv.reader(f, delimiter=';')
            data = []
            for row in content:
                data.append({'id': row[0], 'label': row[1]})
        return data

    def get_meta(self):
        return [{'id': entry['id'],
                 'label': entry['label'],
                 'idx': self.labels2idx[entry['label']]}
                for entry in self.data]

    def __len__(self):
        return len(self.data)

    def iter_data(self, slice_element=None):
        slice_element = slice_element or slice(0, len(self))
        for meta in self.all_meta[slice_element]:
            video_folder = os.path.join(self.folder, str(meta['id']))
            frame_paths = find_images_in_folder(video_folder, formats=['jpg'])
            frames = list(resize_images(frame_paths, self.frame_size))
            result = {'meta': meta,
                      'frames': frames,
                      'id': meta['id']}
            yield result
        else:
            self.write_label2idx_dict()


class ImageListAdapter(AbstractDatasetAdapter):
    """Give a list.txt in format:

        img_path,label_name
        ...

        and it iterates through images/
    """

    def __init__(self, input_file, output_folder, root_folder='',
                 shuffle=False, img_size=-1):
        self.input_file = input_file
        self.output_folder = output_folder
        self.root_folder = root_folder
        self.folder = root_folder
        self.data = ImageListAdapter.parse_paths(self.input_file)
        self.label2idx = self.create_label2idx_dict()
        self.shuffle = shuffle
        self.img_size = img_size
        self.all_meta = self.get_meta()
        if self.shuffle:
            random.shuffle(self.all_meta)

    @staticmethod
    def parse_paths(input_file):
        item_list = [item.strip().split(',')
                     for item in open(input_file, 'r')]
        data = [{'id': os.path.basename(img_path), 'label': label_name,
                 'path': img_path} for img_path, label_name in item_list]
        return data

    def get_meta(self):
        return [{'id': entry['id'],
                 'label': entry['label'],
                 'path': entry['path'],
                 'idx': self.label2idx[entry['label']]}
                for entry in self.data]

    def create_label2idx_dict(self):
        labels = sorted(set([item['label'] for item in self.data]))
        label2idx = {label: label_counter
                     for label_counter, label in enumerate(labels)}
        return label2idx

    def __len__(self):
        return len(self.data)

    def write_label2idx_dict(self):
        json.dump(self.label2idx,
                  open(os.path.join(self.output_folder, 'label2idx.json'),
                       'w'))

    def iter_data(self, slice_element=None):
        slice_element = slice_element or slice(0, len(self))
        self.all_meta = self.all_meta[slice_element]
        for meta in self.all_meta:
            img_path = os.path.join(self.root_folder, str(meta['path']))
            try:
                img = resize_by_short_edge(img_path, self.img_size)
            except ImageNotFound as e:
                print(e)
                continue  # skip the item if image is not readable
            meta.pop('path', None)  # don't store path
            result = {'meta': meta,
                      'frames': [img],
                      'id': meta['id']}
            yield result
        else:
            self.write_label2idx_dict()


class ImageFolderAdapter(AbstractDatasetAdapter):
    r"""Parse the given folder assuming each subfolder is a category and it
    includes the category images.
    """

    def __init__(self, folder, output_folder,
                 file_extensions=['.jpg', '.png'], shuffle=False,
                 img_size=-1):
        self.file_extensions = file_extensions
        self.data = self.parse_folder(folder)
        self.output_folder = output_folder
        self.label2idx = self.create_label2idx_dict()
        self.folder = folder
        self.shuffle = shuffle
        self.img_size = img_size
        self.all_meta = self.get_meta()
        if self.shuffle:
            random.shuffle(self.all_meta)

    def parse_folder(self, folder):
        img_paths = []
        for extension in self.file_extensions:
            search_pattern = os.path.join(folder+"**/*{}".format(extension))
            paths = glob.glob(search_pattern, recursive=True)
        img_paths.extend(paths)
        img_paths = sorted(img_paths)
        data = []
        for img_path in img_paths:
            path = os.path.dirname(img_path)
            category_name = path.split('/')[-1]
            img_name = os.path.basename(img_path)
            category_name = category_name
            data.append({'id': img_name, 'label': category_name, 'path': path})
        return data

    def get_meta(self):
        return [{'id': entry['id'],
                 'label': entry['label'],
                 'path': entry['path'],
                 'idx': self.label2idx[entry['label']]}
                for entry in self.data]

    def create_label2idx_dict(self):
        labels = sorted(set([item['label'] for item in self.data]))
        label2idx = {label: label_counter
                     for label_counter, label in enumerate(labels)}
        return label2idx

    def __len__(self):
        return len(self.data)

    def write_label2idx_dict(self):
        json.dump(self.label2idx,
                  open(os.path.join(self.output_folder, 'label2idx.json'),
                       'w'))

    def iter_data(self, slice_element=None):
        slice_element = slice_element or slice(0, len(self))
        for meta in self.all_meta[slice_element]:
            img_path = os.path.join(self.folder, str(meta['path']),
                                    str(meta['id']))
            img = resize_by_short_edge(img_path, self.img_size)
            result = {'meta': meta,
                      'frames': [img],
                      'id': meta['id']}
            yield result
        else:
            self.write_label2idx_dict()
