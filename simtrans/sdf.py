# -*- coding:utf-8 -*-

"""Reader and writer for SDF format

:Organization:
 AIST

Requirements
------------
* numpy
* lxml xml parser
* jinja2 template engine

Examples
--------

Read SDF model data given the model file

>>> r = SDFReader()
>>> m = r.read('model://pr2/model.sdf')
 
Write simulation model in SDF format

>>> from . import vrml
>>> r = vrml.VRMLReader()
>>> m = r.read('/home/yosuke/HRP-4C/HRP4Cmain.wrl')
>>> w = SDFWriter()
>>> w.write(m, '/tmp/hrp4c.sdf')
>>> import subprocess
>>> subprocess.check_call('gz sdf -k /tmp/hrp4c.sdf'.split(' '))
0
"""

from logging import getLogger
logger = getLogger(__name__)

import os
import subprocess
import tempfile
from copy import deepcopy
import lxml.etree
import numpy
import warnings
with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    from .thirdparty import transformations as tf
import jinja2
from . import model
from . import urdf
from . import collada
from . import stl
from . import utils


class SDFReader(object):
    '''
    SDF reader class
    '''
    def __init__(self):
        self._assethandler = None

    def read(self, fname, assethandler=None):
        '''
        Read SDF model data given the model file
        '''
        self._assethandler = assethandler
        bm = model.BodyModel()
        d = lxml.etree.parse(open(utils.resolveFile(fname)))
        dm = d.find('model')
        bm.name = dm.attrib['name']

        for l in dm.findall('link'):
            # general information
            lm = model.LinkModel()
            lm.name = l.attrib['name']
            pose = l.find('pose')
            if pose is not None:
                self.readPose(lm, pose)
            # phisical property
            inertial = l.find('inertial')
            if inertial is not None:
                lm.mass = float(inertial.find('mass').text)
                pose = inertial.find('pose')
                if pose is not None:
                    lm.centerofmass = [float(v) for v in pose.text.strip(' ').split(' ')][0:3]
                lm.inertia = self.readInertia(inertial.find('inertia'))
            # visual property
            lm.visuals = []
            for v in l.findall('visual'):
                lm.visuals.append(self.readShape(v))
            # contact property
            #collision = l.find('collision')
            #if collision is not None:
            #    lm.collision = self.readShape(collision)
            bm.links.append(lm)

        for j in dm.findall('joint'):
            jm = model.JointModel()
            # general property
            jm.name = j.attrib['name']
            jm.jointType = self.readJointType(j.attrib['type'])
            pose = j.find('pose')
            if pose is not None:
                self.readPose(jm, pose)
            axis = j.find('axis')
            if axis is not None:
                jm.axis = [float(v) for v in axis.find('xyz').text.split(' ')]
                dynamics = axis.find('dynamics')
                if dynamics is not None:
                    damping = dynamics.find('damping')
                    if damping is not None:
                        jm.damping = float(damping.text)
                    friction = dynamics.find('friction')
                    if friction is not None:
                        jm.friction = float(friction.text)
                limit = axis.find('limit')
                if limit is not None:
                    jm.limit = [limit.find('upper').text, limit.find('lower').text]
            jm.parent = j.find('parent').text
            jm.child = j.find('child').text
            # phisical property
            bm.joints.append(jm)

        return bm

    def readPose(self, m, doc):
        pose = numpy.array([float(v) for v in doc.text.split(' ')])
        m.trans = pose[0:3]
        m.rot = tf.quaternion_from_euler(pose[3], pose[4], pose[5])

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
        raise Exception('unsupported joint type: %s' % d)

    def readInertia(self, d):
        inertia = numpy.zeros((3, 3))
        inertia[0, 0] = float(d.find('ixx').text)
        inertia[0, 1] = inertia[1, 0] = float(d.find('ixy').text)
        inertia[0, 2] = inertia[2, 0] = float(d.find('ixz').text)
        inertia[1, 1] = float(d.find('iyy').text)
        inertia[1, 2] = inertia[2, 1] = float(d.find('iyz').text)
        inertia[2, 2] = float(d.find('izz').text)
        return inertia

    def readShape(self, d):
        m = model.ShapeModel()
        m.name = d.attrib['name']
        pose = d.find('pose')
        if pose is not None:
            self.readPose(m, pose)
        for g in d.find('geometry').getchildren():
            if g.tag == 'mesh':
                m.shapeType = model.ShapeModel.SP_MESH
                # print "reading mesh " + mesh.attrib['filename']
                filename = utils.resolveFile(g.find('uri').text)
                fileext = os.path.splitext(filename)[1].lower()
                if fileext == '.dae':
                    reader = collada.ColladaReader()
                elif fileext == '.stl':
                    reader = stl.STLReader()
                else:
                    raise Exception('unsupported mesh format: %s' % fileext)
                submesh = g.find('submesh')
                if submesh is not None:
                    submeshname = submesh.find('name').text
                    submeshcenter = False
                    try:
                        submeshcenter = (submesh.find('center').text.count('true') > 0)
                    except KeyError:
                        pass
                    m.data = reader.read(filename, submesh=submeshname, assethandler=self._assethandler)
                    m.name = m.name + '-' + submeshname
                else:
                    m.data = reader.read(filename, assethandler=self._assethandler)
            elif g.tag == 'box':
                m.shapeType = model.ShapeModel.SP_BOX
                boxsize = [float(v) for v in g.find('size').text.split(' ')]
                m.data = model.BoxData()
                m.data.x = boxsize[0]
                m.data.y = boxsize[1]
                m.data.z = boxsize[2]
            elif g.tag == 'cylinder':
                m.shapeType = model.ShapeModel.SP_CYLINDER
                m.data = model.CylinderData()
                m.data.radius = float(g.find('radius').text)
                m.data.height = float(g.find('length').text)
            elif g.tag == 'sphere':
                m.shapeType = model.ShapeModel.SP_SPHERE
                m.data = model.SphereData()
                m.data.radius = float(g.find('radius').text)
            else:
                raise Exception('unsupported shape type: %s' % g.tag)
        return m


class SDFWriter(object):
    '''
    SDF writer class
    '''
    def __init__(self):
        self._jointparentmap = {}
        self._sensorparentmap = {}
        self._absolutepositionmap = {}


    def write(self, m, f):
        '''
        Write simulation model in SDF format
        '''
        # render the data structure using template
        loader = jinja2.PackageLoader(self.__module__, 'template')
        env = jinja2.Environment(loader=loader)

        # render mesh data to each separate collada file
        cwriter = collada.ColladaWriter()
        swriter = stl.STLWriter()
        dirname = os.path.dirname(f)
        fpath, ext = os.path.splitext(f)
        if ext == '.world':
            m.name = os.path.basename(fpath)
            dirname = fpath
            try:
                os.mkdir(fpath)
            except OSError:
                pass
            template = env.get_template('sdf-model-config.xml')
            with open(os.path.join(dirname, 'model.config'), 'w') as ofile:
                ofile.write(template.render({
                    'model': m
                }))
            template = env.get_template('sdf-world.xml')
            with open(f, 'w') as ofile:
                ofile.write(template.render({
                    'model': m
                }))
            f = os.path.join(dirname, 'model.sdf')

        # render mesh collada file for each links
        for j in m.joints:
            self._jointparentmap[j.child] = j
        for s in m.sensors:
            if s.parent in self._sensorparentmap:
                self._sensorparentmap[s.parent].append(s)
            else:
                self._sensorparentmap[s.parent] = [s]
        try:
            root = utils.findroot(m)[0]
        except IndexError:
            if len(m.joints) == 0:
                root = m.links[0].name
        if m.joints[0].jointType == model.JointModel.J_FIXED:
            m.joints[0].jointType = model.JointModel.J_REVOLUTE
        rootposition = model.TransformationModel()
        # rootposition.trans = rootposition.trans + numpy.array([0, 0, 0.3])  # add offset to the root joint
        self._absolutepositionmap[root] = rootposition
        for cjoint in utils.findchildren(m, root):
            self.convertchildren(m, cjoint)
        template = env.get_template('sdf.xml')
        with open(f, 'w') as ofile:
            ofile.write(template.render({
                'model': m,
                'jointparentmap': self._jointparentmap,
                'sensorparentmap': self._sensorparentmap,
                'absolutepositionmap': self._absolutepositionmap,
                'ShapeModel': model.ShapeModel
            }))

        for l in m.links:
            for v in l.visuals:
                if v.shapeType == model.ShapeModel.SP_MESH:
                    cwriter.write(v, os.path.join(dirname, v.name + ".dae"))
                    swriter.write(v, os.path.join(dirname, v.name + ".stl"))

    def convertchildren(self, mdata, joint):
        absparent = self._absolutepositionmap[joint.parent]
        abschild = model.TransformationModel()
        trans = numpy.dot(tf.quaternion_matrix(absparent.rot), numpy.hstack((joint.gettranslation(), [1])))
        abschild.trans = absparent.trans + trans[0:3]
        abschild.rot = tf.quaternion_multiply(absparent.rot, joint.getrotation())
        joint.axis = numpy.dot(tf.quaternion_matrix(absparent.rot), numpy.hstack((joint.axis, [1])))[0:3]
        self._absolutepositionmap[joint.child] = abschild
        for cjoint in utils.findchildren(mdata, joint.child):
            self.convertchildren(mdata, cjoint)

    def write2(self, m, f):
        '''
        Write simulation model in SDF format
        (internally use urdf and convert to sdf using gz sdf utility)
        '''
        # render the data structure using template
        loader = jinja2.PackageLoader(self.__module__, 'template')
        env = jinja2.Environment(loader=loader)

        # render mesh data to each separate collada file
        dirname = os.path.dirname(f)
        fpath, ext = os.path.splitext(f)
        if ext == '.world':
            m.name = os.path.basename(fpath)
            dirname = fpath
            try:
                os.mkdir(fpath)
            except OSError:
                pass
            template = env.get_template('sdf-model-config.xml')
            with open(os.path.join(dirname, 'model.config'), 'w') as ofile:
                ofile.write(template.render({
                    'model': m
                }))
            template = env.get_template('sdf-world.xml')
            with open(f, 'w') as ofile:
                ofile.write(template.render({
                    'model': m
                }))
            f = os.path.join(dirname, 'model.sdf')

        uwriter = urdf.URDFWriter()
        urdffile = f.replace('.sdf', '.urdf')
        uwriter.write(m, urdffile)
        d = subprocess.check_output(['gz', 'sdf', '-p', urdffile])
        with open(f, 'w') as of:
            of.write(d)

