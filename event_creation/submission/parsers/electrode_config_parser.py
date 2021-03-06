from collections import OrderedDict
import numpy as np
from ..exc import ConfigurationError


class Contact():
    SURFACE_AREAS = dict(
        S=.5,  # TODO: These are all probably wrong!
        G=.5,
        D=.25,
    )

    CSV_FORMAT = '{name},{port_num},{jack_num},{surface_area:.4f},{description}'

    def __init__(self, name, port_num, jack_num, surface_area, description):
        self.name = name
        self.port_num = int(port_num)
        self.jack_num = int(jack_num)
        self.surface_area = float(surface_area)
        self.description = description

    def as_dict(self):
        d = dict(
            name=self.name,
            port_num=self.port_num,
            jack_num=self.jack_num,
            surface_area=self.surface_area,
            description=self.description
        )
        return d

    def as_csv(self):
        return self.CSV_FORMAT.format(**self.as_dict())


class SenseChannel():
    # TODO: What is 'x'?
    CSV_FORMAT = '{contact_name},{name},{contact_num},{ref},x,{description}'

    def __init__(self, contact, name, mux, ref, x, description):
        self.contact = contact
        self.name = name
        self.mux = mux
        self.ref = ref
        self.x = x
        self.description = description

    def as_dict(self):
        d = dict(
            contact=self.contact.as_dict(),
            name=self.name,
            mux=self.mux,
            ref=self.ref,
            description=self.description
        )
        return d

    def as_csv(self):
        return self.CSV_FORMAT.format(contact_name=self.contact.name, contact_num=self.contact.port_num,
                                      **self.as_dict())


class StimChannel():
    # TODO: What is x?
    CSV_FORMAT = 'StimChannel:,{name},x,{comments}\nAnodes:,{anode_csv},#\nCathodes:,{cathode_csv},#'

    def __init__(self, name, anodes, cathodes, comments):
        self.name = name
        self.anodes = [int(x) for x in anodes]
        self.cathodes = [int(x) for x in cathodes]
        self.comments = comments

    def as_dict(self):
        d = dict(
            name=self.name,
            anodes=self.anodes,
            cathodes=self.cathodes,
            comments=self.comments
        )
        return d

    def as_csv(self):
        anode_csv = ','.join([str(a) for a in self.anodes])
        cathode_csv = ','.join([str(c) for c in self.cathodes])
        return self.CSV_FORMAT.format(anode_csv=anode_csv, cathode_csv=cathode_csv, **self.as_dict())


class ElectrodeConfig(object):
    # TODO: SenseChannelSubclasses, StimulationChannelSubclasses??
    CSV_FORMAT = \
        'ODINConfigurationVersion:,#{config_version}#\n' \
        'ConfigurationName:,{config_name}\n' \
        'SubjectID:,{subject_id}\n' \
        'Contacts:\n' \
        '{contacts_csv}\n' \
        'SenseChannelSubclasses:\n' \
        'SenseChannels:\n' \
        '{sense_channels_csv}\n' \
        'StimulationChannelSubclasses:\n' \
        'StimulationChannels:\n' \
        '{stim_channels_csv}\n' \
        '{ref}\n' \
        'EOF\n'

    @property
    def contacts_csv(self):
        return '\n'.join([contact.as_csv() for contact in
                          sorted(list(self.contacts.values()), key=lambda c: c.jack_num)])

    @property
    def sense_channels_csv(self):
        return '\n'.join([sense_channel.as_csv() for sense_channel in
                          sorted(list(self.sense_channels.values()), key=lambda s: s.contact.jack_num)])

    @property
    def stim_channels_csv(self):
        return '\n'.join([stim_channel.as_csv() for stim_channel in
                          sorted(list(self.stim_channels.values()), key=lambda s: s.anodes[0])])

    def as_csv(self):
        if not self.initialized:
            raise ConfigurationError("Config not initialized!")
        return self.CSV_FORMAT.format(
            contacts_csv=self.contacts_csv,
            sense_channels_csv=self.sense_channels_csv,
            stim_channels_csv=self.stim_channels_csv,
            **self.as_dict()
        )

    def __init__(self, filename=None):
        self.electrode_array_dtype = np.dtype(
            [('jack_box_num', '<i8'), ('contact_name', '|S256'), ('port_electrode', '|S256'), ('surface_area', '<f8'),
             ('description', '|S256')])
        self.config_version = None
        self.config_name = None
        self.subject_id = None
        self.contacts = OrderedDict()
        self.sense_channels = OrderedDict()
        self.stim_channels = OrderedDict()
        self.ref = None

        self.parse_fields = dict(
            ODINConfigurationVersion=self.parse_version,
            ConfigurationName=self.parse_name,
            SubjectID=self.parse_id,
            Contacts=self.parse_contacts,
            SenseChannelSubclasses=self.parse_sense_subclasses,
            SenseChannels=self.parse_sense_channels,
            StimulationChannelSubclasses=self.parse_stim_subclasses,
            StimulationChannels=self.parse_stim_channels,
            StimChannel=self.parse_stim_channel,
            REF=self.parse_ref,
            EOF=self.parse_eof
        )

        self.initialized = False

        if filename is not None:
            self.initialize(filename)

    def as_jacksheet(self):
        return {channel.contact.jack_num:channel.contact.name for channel in list(self.sense_channels.values())}

    def get_contact_by_jack_num(self, jack_num):
        for sense_channel in list(self.sense_channels.values()):
            if sense_channel.contact.jack_num == jack_num:
                return sense_channel.contact
        return None

    def as_dict(self):
        contacts = OrderedDict()
        sense_channels = OrderedDict()
        stim_channels = OrderedDict()
        for k, v in list(self.contacts.items()): contacts[k] = v.as_dict()
        for k, v in list(self.sense_channels.items()): contacts[k] = v.as_dict()
        for k, v in list(self.stim_channels.items()): contacts[k] = v.as_dict()
        d = OrderedDict(
            config_version=self.config_version,
            config_name=self.config_name,
            subject_id=self.subject_id,
            contacts=contacts,
            sense_channels=sense_channels,
            stim_channels=stim_channels,
            ref=self.ref
        )
        return d

        # contacts={k:v.as_dict() for k,v in self.contacts.items()},
        # sense_channels={k:v.as_dict() for k,v in self.sense_channels.items()},
        # stim_channels={k:v.as_dict() for k,v in self.stim_channels.items()},

    def sense_channels_as_recarray(self):

        e_array = np.recarray((len(self.sense_channels),), dtype=self.electrode_array_dtype)

        for counter, (chan_name, chan_data) in enumerate(self.sense_channels.items()):
            contact_data = chan_data.contact
            e_array[counter]['jack_box_num'] = int(contact_data.jack_num)
            e_array[counter]['contact_name'] = str(contact_data.name)
            e_array[counter]['port_electrode'] = str(contact_data.port_num)
            e_array[counter]['surface_area'] = float(contact_data.surface_area)
            e_array[counter]['description'] = str(contact_data.description)

        return e_array

    def initialize(self, config_filename):
        with open(config_filename, 'r') as config_file:
            line = next(config_file)
            while (line != False):
                line = line.strip().rstrip(',')
                label = line.split(':')[0]
                if label not in self.parse_fields:
                    raise ConfigurationError("Could not parse line {}".format(line))
                parser = self.parse_fields[label]
                line = parser(line, config_file)

            self.initialized = True

    def intitialize_from_dict(self, contacts_dict, config_name):
        self.config_version = '1.2'
        self.config_name = config_name
        content = list(contacts_dict.values())[0]
        self.subject_id = content['code']
        self.ref = 'REF:,0,common'
        for contact_entry in list(content['contacts'].values()):
            code = contact_entry['code']
            channel = contact_entry['channel']
            area = Contact.SURFACE_AREAS[contact_entry['type']]
            description = contact_entry['description']
            self.contacts[code] = Contact(code, channel, channel, area, '#{}#'.format(description))
            self.sense_channels[code] = SenseChannel(self.contacts[code], code, channel / 32 + 1, '0', 'x',
                                                     '#{}#'.format(description))
        self.initialized = True

    def parse_version(self, line, file):
        self.config_version = line.split(',')[1].strip('#')
        return next(file)

    def parse_name(self, line, file):
        try:
            self.config_name = line.split(',')[1].strip()
        except IndexError:
            self.config_name = ''
        return next(file)

    def parse_id(self, line, file):
        self.subject_id = line.split(',')[1].strip()
        return next(file)

    # def parse_contacts(self, line, file):
    #     line = next(file).strip()
    #     split_line = line.split(',')
    #     while (len(split_line) == 5):
    #         self.contacts[split_line[0]] = Contact(*split_line)
    #         line = next(file).strip()
    #         split_line = line.split(',')
    #     return line
    def parse_contacts(self, line, file):
        line = next(file).strip()
        while ':' not in line:
            split_line = line.split(',')
            self.contacts[split_line[0]] = Contact(*split_line[:5])
            line = next(file).strip()
        return line

    def parse_sense_subclasses(self, line, file):
        # What is this???
        return next(file)

    def parse_sense_channels(self, line, file):
        line = next(file).strip()
        while ':' not in line:
            split_line = line.split(',')
            self.sense_channels[split_line[1]] = \
                SenseChannel(self.contacts[split_line[0]], *split_line[1:6])
            line = next(file).strip()
        return line

    def parse_stim_subclasses(self, line, file):
        # What is this??
        return next(file)

    def parse_stim_channels(self, line, file):
        return next(file)

    def parse_stim_channel(self, line, file):
        split_line = line.split(',')
        name = split_line[1]
        comment = split_line[3]

        # Get anodes
        line = next(file)
        split_line = line.split(':')
        if split_line[0] != 'Anodes':
            raise ConfigurationError("Expected \"Anodes\", found {}".format(split_line[0]))
        split_line = line.split(',')
        anodes = split_line[1:-1]
        if len(anodes) == 0:
            raise ConfigurationError("Found no anodes for stim channel {}".format(name))

        # Get cathodes
        line = next(file)
        split_line = line.split(':')
        if split_line[0] != "Cathodes":
            raise ConfigurationError("Expected \"Cathodes\", found {}".format(split_line[0]))
        split_line = line.split(',')
        cathodes = split_line[1:-1]
        if len(cathodes) == 0:
            raise ConfigurationError("Found no cathodes for stim channel {}".format(name))

        if len(cathodes) != len(anodes):
            raise ConfigurationError("Number of anodes ({}) "
                                             "did not match number of cathodes ({})".format(len(anodes), len(cathodes)))

        self.stim_channels[name] = StimChannel(name, anodes, cathodes, comment)

        return next(file)

    def parse_ref(self, line, file):
        split_line = line.split(',')
        self.ref = line
        return next(file)

    def parse_eof(self, line, file):
        return False


def test_as_csv():
    import difflib
    ec = ElectrodeConfig()
    # csv_file = r"C:\Users\OdinUser\Desktop\configurations\ThisIsSubjectId_ThisIsConfigName.csv"
    csv_file = r"/Users/iped/DARPA/ram_tasks/data/PS2/Ass/session_37/host_pc/20161222_142722/config_files/SubjID_TwoStimChannels.csv"
    csv_contents = open(csv_file).read()
    ec.initialize(csv_file)
    if not csv_contents == ec.as_csv():
        print((''.join(difflib.ndiff(csv_contents.splitlines(True), ec.as_csv().splitlines(True)))))
        #   assert False, "CSV not replicated!"
    else:
        print("CSV successfully replicated")
    return ec


def test_from_dict():
    import json
    ec = ElectrodeConfig()
    contacts_dict = json.load(open(r"C:\OdinWiFiServer\ns2\montage\contacts.json"))
    ec.intitialize_from_dict(contacts_dict, "FromJson")
    csv_out = ec.as_csv()
    open(r"C:\OdinWiFiServer\ns2\montage\contacts.csv", 'w').write(csv_out)


if __name__ == '__main__':
    from pprint import pprint

    ec = test_as_csv()
    # test_from_dict()
    # pprint(ec.as_dict())
    # print(ec.as_csv())
