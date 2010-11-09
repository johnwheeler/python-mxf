# -*- coding: utf-8 -*-

""" Implements basic classes to parse SMPTE S377-1-2009 compliant MXF files. """

import re

from mxf.common import InterchangeObject, OrderedDict, Singleton
from mxf.rp210 import RP210Exception, RP210
from mxf.rp210types import Array, Reference, Integer

class S377MException(Exception):
    """ Raised on non SMPTE 377M input. """

    def __init__(self, error):
        """ Init Method """
        Exception.__init__(self, 'S377M: ' + error)


class KLVFill(InterchangeObject):
    """ KLVFill parser. """

    def __init__(self, fdesc, debug=False):
        InterchangeObject.__init__(self, fdesc, debug)

    def __str__(self):
        return "<KLVFill pos=%d size=%d>" % (self.pos, self.length)

    def read(self):
        """ KLV Fill data has no value. """

        if self.debug:
            print "data:", self.fdesc.read(self.length).encode('hex_codec')
        else:
            self.data = self.fdesc.read(self.length)

    def write(self):
        self.fdesc.write(self.key + self.ber_encode_length(len(self.data), bytes_num=8).decode('hex_codec') + self.data)

class KLVDarkComponent(KLVFill):
    """ Generic Dark data handler class. """

    def __init__(self, fdesc, debug=False):
        KLVFill.__init__(self, fdesc, debug)

    def __str__(self):
        return "<KLVDarkComponent pos=%d size=%d ul=%s >" % (self.pos, self.length, self.key.encode('hex_codec'))


class MXFPartition(InterchangeObject):
    """ MXF Partition Pack parser. """

    part_items = [
        ('major_version',       2),
        ('minor_version',       2),
        ('kag_size',            4),
        ('this_partition',      8),
        ('previous_partition',  8),
        ('footer_partition',    8),
        ('header_byte_count',   8),
        ('index_byte_cout',     8),
        ('index_sid',           4),
        ('body_offset',         8),
        ('body_sid',            4),
        ('operational_pattern', 16),
    ]

    def __init__(self, fdesc, debug=False):
        InterchangeObject.__init__(self, fdesc, debug)
        self.data = {'essence_containers': [], }

        if not re.search('060e2b34020501010d01020101(0[2-4])(0[0-4])00', self.key.encode('hex_codec')):
            raise S377MException('Not a valid Partition Pack key: %s' % self.key.encode('hex_codec'))

    def __str__(self):
        return '<MXF%(type)sPartition pos=%(pos)s %(openness)s and %(completeness)s>' % {
            'pos': self.pos,
            'type': {'\x02': 'Header', '\x03': 'Body', '\x04': 'Footer'}[self.key[13]],
            'openness': ord(self.key[14]) & 0xfe and 'Closed' or 'Open',
            'completeness': ord(self.key[14]) & 0xfd and 'Complete' or 'Incomplete',
        }

    def __smtpe_377m_check(self):
        """ Check conformance to SMTPE 377M 2004. """

        if self.data['major_version'].encode('hex_codec') != '0001':
            raise S377MException('Invalid Major version for Partition Pack')
        if self.data['minor_version'].encode('hex_codec') not in ('0002', '0003'):
            raise S377MException('Invalid Minor version for Partition Pack')

        # Header Partition Pack checks
        if self.key[14] == '\x02':
            if self.data['this_partition'] != 8 * '\x00':
                raise S377MException('Invalid value for ThisPartition in Header Partition Pack')
            if self.data['previous_partition'] != 8 * '\x00':
                raise S377MException('Invalid value for PreviousPartition in Header Partition Pack')
        # partition_info['operational_pattern'][13] -> 10h –7Fh specialized pattern

        # Footer Patition Pack checks
        if self.key[14] == '\x04':
            if not ord(self.key[14]) & 0xfe:
                raise S377MException('Open Footer Partition is not allowed')

        if len(self.data['essence_containers']) == 0 and self.data['body_sid'] != 4 * '\x00':
            raise S377MException('Invalid value for BodySID in Partition Pack')

    def read(self):
        idx = 0
        data = self.fdesc.read(self.length)

        # Read Partition Pack items
        for pp_item, pp_item_size in self.part_items:
            self.data[pp_item] = data[idx:idx+pp_item_size]
            idx += pp_item_size

        # Read essence containers list, if any
        self.data['essence_containers'] = Array(data[idx:], 'StrongReferenceArray').read()

        self.__smtpe_377m_check()

        if self.debug:
            print "%d essences in partition:" % len(self.data['essence_containers'])

        return

    def write(self):
        ret = ""
        for pp_item, _ in self.part_items:
            ret += self.data[pp_item]

        ret += Array(self.data['essence_containers'], 'StrongReferenceArray').write()

        self.fdesc.write(self.key + self.ber_encode_length(len(ret), bytes_num=8).decode('hex_codec') + ret)
        return

    def human_readable(self):
        for key, item in self.data.items():
            if key == 'essence_containers':
                for i, essence in enumerate(item):
                    print "Essence %d: " % i, essence.read()
            else:
                print "%s: %s" % (key, item.encode('hex_codec'))
        return


class MXFPrimer(InterchangeObject):
    """ MXF Primer Pack parser. """

    def __init__(self, fdesc, rp210=None, debug=False):
        InterchangeObject.__init__(self, fdesc, debug)
        self.data = OrderedDict()

        if rp210:
            self.rp210 = rp210
        else:
            self.rp210 = Singleton(RP210)

        if self.key and not re.search('060e2b34020501..0d01020101050100', self.key.encode('hex_codec')):
            raise S377MException('Not a valid Primer Pack key: %s' % self.key.encode('hex_codec'))


    def __str__(self):
        ret = ['<MXFPrimer']
        ret += ['pos=%d' % self.pos]
        ret += ['size=%d' % self.length]
        ret += ['localtags=%d' % len(self.data)]
        if self.debug:
            ret += ['\n']
            for i, j in self.data.items():
                ret += ['%s: %s\n' % (i.encode('hex_codec'), j.encode('hex_codec'))]
        return ' '.join(ret) + '>'

    @staticmethod
    def customize(primer, spec, mappings=None):
        """ Modifies a primer to abide @spec rules with optional @mappings.

        @spec: instance of a mxf.rp210 like object
        @mappings: a dictionary that is passed to inject method

        @returns: custimized Primer object.
        """

        import copy
        aprim = copy.copy(primer)

        if mappings:
            spec.inject(mappings)

        aprim.data = {}
        aprim.data.update(primer.data)
        aprim.rp210 = spec

        if mappings:
            aprim.inject(mappings.keys())

        return aprim

    def inject(self, mappings):
        """ Insert new mappings in Primer.

        Allows insertion of new local tag to format UL mappings with their
        RP210 basic type.
        """

        for item in mappings:
            self.data[item.decode('hex_codec')] = item.rjust(32, '0').decode('hex_codec')
        return

    def read(self):

        data = self.fdesc.read(self.length)

        lt_list_size = Integer(data[0:4], 'UInt32').read()
        lt_item_size = Integer(data[4:8], 'UInt32').read()

        idx = 8
        while lt_list_size > len(self.data):
            self.data[data[idx:idx+2]] = Reference(data[idx+2:idx+lt_item_size], 'Universal Label').read()
            idx += lt_item_size

        if self.debug:
            print "%d local tag mappings of %d size in Primer Pack" % (lt_list_size, lt_item_size)

        return

    def write(self):

        ret = ""
        for tag, ful in self.data.items():
            ret += tag + Reference(ful, 'Universal Label').write()

        lt_list_size = Integer(len(self.data), 'UInt32').write()
        lt_item_size = Integer(len(ret) / len(self.data), 'UInt32').write()
        ret = lt_list_size + lt_item_size + ret

        self.fdesc.write(self.key + self.ber_encode_length(len(ret), bytes_num=8).decode('hex_codec') + ret)
        return

    def decode_from_local_tag(self, tag, value):
        """ Decode data according to local tag mapping to format Universal Labels. """

        etag = tag.encode('hex_codec')
        evalue = value.encode('hex_codec')

        if tag not in self.data.keys():
            return "Error: Local key '%s' not found in primer (%s)" % (etag, evalue)

        #if not self.data[tag].startswith('060e2b34'.decode('hex_codec')):
        #    return "Error: '%s' does not map to a SMPTE format UL '%s'" % (etag, self.data[tag].encode('hex_codec'))

        key = self.rp210.get_triplet_from_format_ul(self.data[tag])[1]
        # SMTPE RP 210 conversion
        try:
            return key, self.rp210.convert(self.data[tag], value)
        except RP210Exception:
            return key, evalue

    def encode_from_local_tag(self, tag, value):
        """ Encode data according to local tag mapping to format Universal Labels. """

        etag = tag.encode('hex_codec')

        if tag not in self.data.keys():
            return "Error: Local key '%s' not found in primer" % etag

        # SMTPE RP 210 conversion
        try:
            return tag, self.rp210.convert(self.data[tag], value)
        except RP210Exception:
            return tag, value


class MXFDataSet(InterchangeObject):
    """ MXF parsing class specialized for loading Sets and Packs. """

    dataset_names = {
         # SMPTE 377M: Strutural Metadata Sets
         '060e2b34025301010d01010101010900': 'Filler',
         '060e2b34025301010d01010101010f00': 'Sequence',

         '060e2b34025301010d01010101011100': 'SourceClip',
         '060e2b34025301010d01010101011400': 'TimecodeComponent',

         '060e2b34025301010d01010101012300': 'EssenceContainerData',
         '060e2b34025301010d01010101012800': 'CDCIEssenceDescriptor',

         '060e2b34025301010d01010101011800': 'ContentStorage',

         '060e2b34025301010d01010101012e00': 'EssenceDescription',
         '060e2b34025301010d01010101013000': 'Identification',

         '060e2b34025301010d01010101013600': 'MaterialPackage',
         '060e2b34025301010d01010101013700': 'SourcePackage',

         '060e2b34025301010d01010101013b00': 'TimelineTrack',
         '060e2b34025301010d01010101013f00': 'TaggedValue', # Avid Dark 2

         '060e2b34025301010d01010101014200': 'GenericSoundEssenceDescriptor',
         '060e2b34025301010d01010101014400': 'MultipleDescriptor',
         '060e2b34025301010d01010101014800': 'WaveAudioDescriptor',
    }

    def __init__(self, fdesc, primer, debug=False, dark=False):
        InterchangeObject.__init__(self, fdesc, debug)
        self.primer = primer
        self.dark = dark
        self.data = {
            'by_tag': OrderedDict(),
            'by_format_ul': OrderedDict(),
        }
        self.set_type = 'DataSet'

        if self.key.encode('hex_codec') not in MXFDataSet.dataset_names.keys():
            #print "MXFDataSet is dark", self.key.encode('hex_codec')
            self.dark = True
            self.set_type = 'Dark' + self.set_type
        else:
            self.set_type = MXFDataSet.dataset_names[self.key.encode('hex_codec')]

        if not self.dark:
            if not self.key.encode('hex_codec').startswith('060e2b34'):
                raise S377MException('Not a SMPTE administrated label')

            if self.key[4] != '\x02':
                raise S377MException('Not an MXF Set/Pack')

            if self.key[5] != '\x53':
                raise S377MException('Non-Local set syntax not supported yet (0x%x)' % ord(self.key[5]))

    def __str__(self):
        ret = ['<MXF' + self.set_type]
        ret += ['pos=%d' % self.pos]
        ret += ['size=%d' % self.length]
        ret += ['InstanceUID=%s' % self.i_guid]
        if self.debug:
            ret += ['tags=%d:\n' % len(self.data['by_tag']) \
                + '\n'.join(["%s: %s %d bytes" % (
                    i.encode('hex_codec'),
                    j.encode('hex_codec').ljust(64, ' ')[:64],
                    len(j)
                ) for i, j in self.data['by_tag'].items()])]
        return ' '.join(ret) + '>'

    def __getattribute__(self, attr):
        if attr.startswith('i_'):
            data = object.__getattribute__(self, 'data')
            if data and 'by_format_ul' in data and attr[2:] in data['by_format_ul']:
                return data['by_format_ul'][attr[2:]]

        return object.__getattribute__(self, attr)

    def read(self):
        """ Generic read method for sets and packs. """

        idx = 0
        data = self.fdesc.read(self.length)

        # Get all items
        offset = idx
        while offset < idx + self.length:
            set_size = Integer(data[offset+2:offset+4], 'UInt16').read()
            localtag = data[offset:offset+2]
            localdata = data[offset+4:offset+set_size+4]
            offset += set_size + 4

            cvalue = None
            key_name = localtag.encode('hex_codec')
            try:
                key_name, cvalue = self.primer.decode_from_local_tag(localtag, localdata)
            except KeyError, _error:
                print "Primer Pack is missing an entry for:", localtag.encode('hex_codec')

            except RP210Exception, _error:
                print "Could not convert to [data:%s] format %s" % (localdata.encode('hex_codec'), self.primer.data[localtag].encode('hex_codec'))
                cvalue = "[data:%s]" % localdata.encode('hex_codec')

            self.data['by_tag'].update({localtag: cvalue})
            self.data['by_format_ul'].update({key_name: cvalue})

        return

    def write(self):

        ret = []
        for tag, value in self.data['by_tag'].items():
            # Not all values are decoded
            if isinstance(value, basestring):
                localtag = tag
                cvalue = value.decode('hex_codec')
            else:
                localtag, conv = self.primer.encode_from_local_tag(tag, value.read())
                cvalue = conv.write()
            ret.append(localtag + self.ber_encode_length(len(cvalue), bytes_num=2, prefix=False).decode('hex_codec') + cvalue)

        ret = ''.join(ret)
        self.fdesc.write(self.key + self.ber_encode_length(len(ret), bytes_num=8).decode('hex_codec') + ret)
        return

    def human_readable(self, klv_hash=None, indent=None):

        if not indent:
            indent = 0

        print "%s%s" % (4 * indent * ' ', self)

        for i, j in self.data['by_format_ul'].items():

            if i == 'guid':
                continue

            elif isinstance(j, Reference):
                if j.subtype in ('AUID', 'PackageID'):
                    print "%s%s: %s" % (4 * indent * ' ' + '  ', i, j)
                elif j.read() not in klv_hash:
                    print "%s%s: broken reference, %s %s" % (4 * indent * ' ' + '  ', i, j, j.subtype)
                elif not klv_hash[j.read()]['used']:
                    klv_hash[j.read()]['used'] = True
                    klv_hash[j.read()]['klv'].human_readable(klv_hash, indent+1)
                else:
                    print "%s%s: <-> %s" % (4 * indent * ' ' + '  ', i, j)

            elif isinstance(j, Array):
                if j.subconv is Reference:
                    for k in j.read():
                        if j.subtype == 'AUID':
                            print "%s%s: %s" % (4 * indent * ' ' + '  ', i, Reference(k))
                        elif k not in klv_hash:
                            print "%s%s: broken reference, %s" % (4 * indent * ' ' + '  ', i, Reference(k))
                        elif not klv_hash[k]['used']:
                            print ""
                            klv_hash[k]['used'] = True
                            klv_hash[k]['klv'].human_readable(klv_hash, indent+1)
                        else:
                            print "%s%s: <-> %s" % (4 * indent * ' ' + '  ', i, Reference(k))
                else:
                    for k in j.read():
                        print "%s%s: %s" % (4 * indent * ' ' + '  ', i, k)
            else:
                print "%s%s: %s %s" % (4 * indent * ' ' + '  ', i, j, type(j))

        return klv_hash


class MXFPreface(MXFDataSet):
    """ MXF Metadata Preface parser. """

    def __init__(self, fdesc, debug=False):
        MXFDataSet.__init__(self, fdesc, debug)
        self.set_type = 'Preface'


class RandomIndexMetadata(InterchangeObject):
    """ MXF Random Index Pack metadata parser. """

    def __init__(self, fdesc, debug=False):
        InterchangeObject.__init__(self, fdesc, debug)
        self.data = {'partition': []}

    def __str__ (self):
        return '<RandomIndexMetadata pos=%d size=%d entries=%d>' % (self.pos, self.length, len(self.data['partition']))

    def read(self):

        idx = 0
        data = self.fdesc.read(self.length)

        for _ in range(0, (self.length - 4) / 12):
            self.data['partition'].append({
                'body_sid': data[idx:idx+4],
                'byte_offset': data[idx+4:idx+12],
            })
            idx += 12

        total_part_length = Integer(data[idx:idx+4], 'UInt32').read()

        if 16 + self.bytes_num + self.length != total_part_length:
            raise S377MException('Overall length differs from UL length')
        return

    def write(self):
        ret = ""
        for partition in self.data['partition']:
            ret += partition['body_sid'] + partition['byte_offset']

        total_part_length = Integer(16 + 9 + 4 + len(ret), 'UInt32').write()

        self.fdesc.write(self.key + self.ber_encode_length(len(ret) + 4, bytes_num=8).decode('hex_codec') + ret + total_part_length)
        return


