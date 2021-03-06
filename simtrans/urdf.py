# -*- coding:utf-8 -*-

"""Reader and writer for URDF format

:Organization:
 AIST

Requirements
------------
* numpy
* lxml xml parser
* jinja2 template engine

Examples
--------

Read URDF model data given the model file

>>> r = URDFReader()
>>> m = r.read('package://atlas_description/urdf/atlas_v3.urdf')

Write simulation model in URDF format

>>> from . import vrml
>>> r = vrml.VRMLReader()
>>> m = r.read('/home/yosuke/HRP-4C/HRP4Cmain.wrl')
>>> w = URDFWriter()
>>> w.write(m, '/tmp/hrp4c.urdf')
>>> import subprocess
>>> subprocess.check_call('check_urdf /tmp/hrp4c.urdf'.split(' '))
0
"""

import os
import lxml.etree
import numpy
import re
import warnings
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    from .thirdparty import transformations as tf
import jinja2
import uuid
from . import model
from . import collada
from . import stl
from . import utils


class URDFReader(object):
    '''
    URDF reader class
    '''
    def __init__(self):
        self._assethandler = None

    def read(self, fname, assethandler=None):
        """Read URDF model data given the model file

        :param fname: path of the file to read
        :param assethandler: asset handler (optional)
        :returns: model data
        :rtype: model.Model

        """
        if assethandler is not None:
            self._assethandler = assethandler

        bm = model.BodyModel()
        d = lxml.etree.parse(open(utils.resolveFile(fname)))

        for l in d.findall('link'):
            # general information
            lm = model.LinkModel()
            lm.name = l.attrib['name']
            # phisical property
            inertial = l.find('inertial')
            if inertial is not None:
                lm.mass = float(inertial.find('mass').attrib['value'])
                lm.centerofmass = [float(v) for v in inertial.find('origin').attrib['xyz'].split(' ')]
                lm.inertia = self.readInertia(inertial.find('inertia'))
            # visual property
            lm.visuals = []
            for v in l.findall('visual'):
                lm.visuals.append(self.readShape(v))
            # contact property
            lm.collisions = []
            for c in l.findall('collision'):
                lm.collisions.append(self.readShape(c))
            bm.links.append(lm)

        for j in d.findall('joint'):
            jm = model.JointModel()
            # general property
            jm.name = j.attrib['name']
            jm.jointType = self.readJointType(j.attrib['type'])
            origin = j.find('origin')
            if origin is not None:
                self.readOrigin(jm, origin)
            axis = j.find('axis')
            if axis is not None:
                jm.axis = [float(v) for v in axis.attrib['xyz'].split(' ')]
            jm.parent = j.find('parent').attrib['link']
            jm.child = j.find('child').attrib['link']
            # phisical property
            dynamics = j.find('dynamics')
            if dynamics is not None:
                try:
                    jm.damping = float(dynamics.attrib['damping'])
                    jm.friction = float(dynamics.attrib['friction'])
                except KeyError:
                    pass
            limit = j.find('limit')
            if limit is not None:
                try:
                    jm.limit = [float(limit.attrib['upper']), float(limit.attrib['lower'])]
                    velocity = float(limit.attrib['velocity'])
                    jm.velocitylimit = [velocity, -velocity]
                except KeyError:
                    pass
            bm.joints.append(jm)

        return bm

    def readOrigin(self, m, doc):
        try:
            m.trans = numpy.array([float(v) for v in re.split(' +', doc.attrib['xyz'].strip(' '))])
        except KeyError:
            pass
        try:
            rpy = [float(v) for v in re.split(' +', doc.attrib['rpy'].strip(' '))]
            m.rot = tf.quaternion_from_euler(rpy[0], rpy[1], rpy[2])
        except KeyError:
            pass

    def readJointType(self, d):
        if d == "fixed":
            return model.JointModel.J_FIXED
        elif d == "revolute":
            return model.JointModel.J_REVOLUTE
        elif d == 'prismatic':
            return model.JointModel.J_PRISMATIC
        elif d == 'screw':
            return model.JointModel.J_SCREW
        elif d == 'continuous':
            return model.JointModel.J_CONTINUOUS
        raise Exception('unsupported joint type %s' % d)

    def readInertia(self, d):
        inertia = numpy.zeros((3, 3))
        inertia[0, 0] = float(d.attrib['ixx'])
        inertia[0, 1] = inertia[1, 0] = float(d.attrib['ixy'])
        inertia[0, 2] = inertia[2, 0] = float(d.attrib['ixz'])
        inertia[1, 1] = float(d.attrib['iyy'])
        inertia[1, 2] = inertia[2, 1] = float(d.attrib['iyz'])
        inertia[2, 2] = float(d.attrib['izz'])
        return inertia

    def readShape(self, d):
        sm = model.ShapeModel()
        sm.name = 'shape-' + str(uuid.uuid1()).replace('-', '')
        origin = d.find('origin')
        if origin is not None:
            self.readOrigin(sm, origin)
        for g in d.find('geometry').getchildren():
            if g.tag == 'mesh':
                sm.shapeType = model.ShapeModel.SP_MESH
                # print "reading mesh " + mesh.attrib['filename']
                filename = utils.resolveFile(g.attrib['filename'])
                fileext = os.path.splitext(filename)[1].lower()
                if fileext == '.dae':
                    reader = collada.ColladaReader()
                else:
                    reader = stl.STLReader()
                sm.data = reader.read(filename, assethandler=self._assethandler)
                try:
                    scales = [float(v) for v in g.attrib['scale'].split(' ')]
                    if scales[0] != 0.0:
                        d = model.MeshTransformData()
                        d.matrix = tf.scale_matrix(scales[0])
                        d.children = [sm.data]
                        sm.data = d
                except KeyError:
                    pass
            elif g.tag == 'box':
                sm.shapeType = model.ShapeModel.SP_BOX
                sm.data = model.BoxData()
                boxsize = [float(s) for s in g.attrib['size'].split(' ')]
                sm.data.x = boxsize[0]
                sm.data.y = boxsize[1]
                sm.data.z = boxsize[2]
            elif g.tag == 'cylinder':
                sm.shapeType = model.ShapeModel.SP_CYLINDER
                sm.data = model.CylinderData()
                sm.data.radius = float(g.attrib['radius'])
                sm.data.height = float(g.attrib['length'])
            elif g.tag == 'sphere':
                sm.shapeType = model.ShapeModel.SP_SPHERE
                sm.data = model.SphereData()
                sm.data.radius = float(g.attrib['radius'])
            else:
                raise Exception('unsupported shape type: %s' % g.tag)
        return sm


class URDFWriter(object):
    '''
    URDF writer class
    '''
    def write(self, m, f):
        """Write simulation model in URDF format

        :param m: model data
        :param f: path of the file to save
        :returns: None
        :rtype: None

        """
        # render the data structure using template
        loader = jinja2.PackageLoader(self.__module__, 'template')
        env = jinja2.Environment(loader=loader)

        # render mesh data to each separate collada file
        cwriter = collada.ColladaWriter()
        swriter = stl.STLWriter()
        dirname = os.path.dirname(f)
        for l in m.links:
            for v in l.visuals:
                if v.shapeType == model.ShapeModel.SP_MESH:
                    cwriter.write(v, os.path.join(dirname, v.name + ".dae"))
                    swriter.write(v, os.path.join(dirname, v.name + ".stl"))

        # render mesh collada file for each links
        template = env.get_template('urdf.xml')
        with open(f, 'w') as ofile:
            ofile.write(template.render({
                'model': m,
                'ShapeModel': model.ShapeModel,
                'JointModel': model.JointModel,
                'tf': tf
            }))
