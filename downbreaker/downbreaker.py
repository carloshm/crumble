# -*- coding: utf-8 -*-

try:
    import pefile  # easy information about PE file like header information
    import capstone  # capstone lib for OPcodes
    import Queue  # queue module for "recursive" attempt
    import threading  # threading module for some performance optimizations
    import binascii  # using for hexdump
    import sys
    import simplejson as json  # json is used for saving the results of the disassembly
    import tempfile
    import argparse  # programm arguments
    import os  # needed for some filecontentcleaning
except ImportError:
    sys.exit('Missing dependencies, please check readme.')


def get_hexdump_and_entrypoint_from_file(filename):
    try:
        pe = pefile.PE(filename)
    except OSError:
        sys.exit('The file: ' + filename + ' could not be found.')
    return binascii.b2a_hex(pe.get_memory_mapped_image()), pe.OPTIONAL_HEADER.AddressOfEntryPoint


def do_disassembly(address_ptr, dsm_queue, address_map, full_hexdump, res_file, functionname=None):
    indirect_controlflows = 0  # +1 if problematic controlflow // not yet handled

    conditional_branch = ['jo', 'jno', 'jb', 'jnae', 'jc', 'jnb', 'jae', 'jnc', 'jz', 'je', 'jnz',
                          'jne', 'jbe', 'jna', 'jnbe', 'ja', 'js', 'jns', 'jp', 'jpe', 'jnp', 'jpo',
                          'jl', 'jnge', 'jnl', 'jge', 'jle', 'jng', 'jnle', 'jg']
    function_call = ['call', 'callf']
    unconditional_branch = ['jmp', 'jmpf']
    return_instr = ['ret']

    mode = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)  # set architecture to x86 (32 bit)

    inbasicblock = True
    startofbasicblock = True
    startaddr = 0
    functionentries = 0
    basicblock = []

    if hex(address_ptr) not in address_map:
        address_map.append(hex(address_ptr))  # mark address as visited
        if functionname is None:
            functionname = address_ptr

        while inbasicblock:
            if startofbasicblock:
                startofbasicblock = False
                startaddr = address_ptr

            buff = binascii.a2b_hex(full_hexdump[get_string_pointer(address_ptr):get_string_pointer(address_ptr + 7)])
            if len(list(mode.disasm(buff, address_ptr))) == 0:  # check if end of instructions
                inbasicblock = False
            else:
                address_ptr_first_instruction = address_ptr
                for instruction in mode.disasm(buff, address_ptr):
                    if instruction.address == address_ptr_first_instruction:  # process only the first instruction found

                        disassembled = hex(instruction.address) + ' ' + instruction.mnemonic + ' ' + instruction.op_str
                        basicblock.append(disassembled)
                        if len(basicblock) > 1:
                            if basicblock[len(basicblock) - 2].find("push ebp") > 0 \
                                    and basicblock[len(basicblock) - 1].find("mov ebp, esp") > 0:
                                functionentries += 1

                        if instruction.mnemonic in unconditional_branch:
                            inbasicblock = False
                            if instruction.op_str.find('dword ptr') != -1:  # indirect (ptr)
                                indirect_controlflows += 1
                            elif instruction.op_str.find('0x') == -1:  # indirect (registers)
                                indirect_controlflows += 1
                            else:
                                dsm_queue.put([int(instruction.op_str, 16), functionname])

                        elif instruction.mnemonic in function_call:
                            address_ptr += instruction.size
                            if instruction.op_str.find('dword ptr') != -1:
                                indirect_controlflows += 1
                            elif instruction.op_str.find('0x') == -1:
                                indirect_controlflows += 1
                            else:
                                dsm_queue.put([int(instruction.op_str, 16)])

                        elif instruction.mnemonic in conditional_branch:
                            inbasicblock = False
                            dsm_queue.put([address_ptr + instruction.size, functionname])
                            dsm_queue.put([int(instruction.op_str, 16), functionname])

                        elif instruction.mnemonic in return_instr:
                            inbasicblock = False

                        elif functionentries > 1:  # another function entry - "backtracing" does not check for lea's
                            inbasicblock = False
                            del basicblock[len(basicblock) - 2:len(basicblock)]

                        else:
                            address_ptr += instruction.size
        put_data_in_json_file(basicblock, hex(startaddr), hex(functionname), res_file)


def get_string_pointer(address):
    return address * 2


def worker(dsm_queue, address_map, full_hexdump, res_file):
    while True:
        queue_contents = dsm_queue.get()
        entry_point = queue_contents[0]

        if len(queue_contents) == 2:
            functionname = queue_contents[1]
        else:
            functionname = None

        do_disassembly(entry_point, dsm_queue, address_map, full_hexdump, res_file, functionname)
        dsm_queue.task_done()


def find_all(a_str, sub):
    start = 0
    while True:
        start = a_str.find(sub, start)
        if start == -1:
            return
        yield start / 2
        start += len(sub)


def parse_arguments():
    parser = argparse.ArgumentParser(description='Crossplatform commandline tool, written in Python, '
                                                 'which can disassemble 32bit PE files.')
    parser.add_argument('-filename', action="store", dest='pe_filename',
                        help='set the filename')
    parser.add_argument('-hybrid', action="store_true", default=False,
                        help='turn on hybrid processing (linear sweep + recursive traversal)')
    parser.add_argument('-threads', action="store", dest="num_threads", type=int, default=1,
                        help='set number of threads used for disassembly')
    parser.add_argument('-saveto', action='store', dest='res_filename', default='disassembled',
                        help='set the name of the output JSON file')
    return parser.parse_args()


def get_res_file_handle(res_filename):
    ending = '.json'
    try:
        res = open(res_filename + ending, 'w+')
    except IOError:
        sys.exit('IOError while accessing file ' + res_filename + ending + '.')
    else:
        return res


def put_data_in_json_file(basicblock, basicblockaddress, functionaddress, filename):
    functionname = 'func_' + functionaddress
    basicblockname = 'basicblock_' + basicblockaddress

    filecontent = json.load(filename)
    filename.flush()
    filename.seek(0)

    # todo: check if function allready exists
    # if filecontent.find(functionname) > 0:
    # handle it

    data = {functionname: [{basicblockname: basicblock}]}
    filecontent.append(data)
    json.dump(filecontent, filename)
    filename.flush()
    filename.seek(0)


# def print_json_file_pretty(filename):
    # add load stuff
    # for op in filename[0]['func_0x001'][0]['basicblock_0x001']:
    #    print(op)
    # todo!

def main():
    arguments = parse_arguments()
    address_map = []  # saves already visited adresses

    res_file = get_res_file_handle(arguments.res_filename)
    json.dump([], res_file)
    res_file.flush()
    res_file.seek(0)

    dsm_queue = Queue.Queue()
    full_hexdump, first_entry_point = get_hexdump_and_entrypoint_from_file(arguments.pe_filename)

    for i in range(int(arguments.num_threads)):
        t = threading.Thread(target=worker, args=(dsm_queue, address_map, full_hexdump, res_file))
        t.daemon = True
        t.start()

    print("\nStarting disassembly..\n")

    dsm_queue.put([first_entry_point])
    if arguments.hybrid:
        for entrypoint in list(find_all(full_hexdump, "5589e5")):
            dsm_queue.put([entrypoint])

    dsm_queue.join()  # wait for all jobs to finish

    # print_json_file_pretty(res_file)
    print(json.load(res_file))

    print("Successfully disassembled " + str(len(address_map)) + " Basicblocks.")
    res_file.close()
