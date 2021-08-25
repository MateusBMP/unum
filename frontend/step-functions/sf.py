#!/usr/bin/env python
import json, os, sys, subprocess, time
import argparse
import yaml
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper

from cfn_tools import load_yaml, dump_yaml

unum_map_counter = 0
unum_parallel_counter = 0

def lambda_state(state):
    if ':' not in state["Resource"]:
        return True

    # AWS arn
    if state["Resource"].split(':')[2] == 'lambda':
        return True

    return False

def get_state_unum_function_name(state):
    ''' Given a Step Functions Task state, return the Lambda function's unum
    name

    @param state dict

    @return str
    '''
    if 'arn:aws:lambda' in state["Resource"]:
        try:
            with open('function-arn.yaml') as f:
                arn_to_name_mapping = load_yaml(f.read())
        except Exception as e:
            raise e

        try:
            function_name = list(arn_to_name_mapping.keys())[list(arn_to_name_mapping.values()).index(state["Resource"])]
        except Exception as e:
            raise e
        return function_name

    else:
        return state["Resource"]


def _translate_state_machine(state_name, state_machine):
    ''' Given a state, compute the IR, Entry function's config and exit
    function's config for the downstream state machine
    '''

    state = state_machine["States"][state_name]
    ir = []

    if state["Type"] == "Task":
        unum_function_name = get_state_unum_function_name(state)
        # print(f'{state_name}: {state}\nFunction name: {unum_function_name}')
        if "End" in state and state["End"] == True:

            config = {"Name": unum_function_name}
            ir.append(config)
            return {
                "State Name": state_name,
                "unum IR": ir,
                "Entry unum function": config,
                "Exit unum function": config
            }

        elif "Next" in state:

            next_state = _translate_state_machine(state["Next"], state_machine)

            config = {
                "Name": unum_function_name,
                "Next": next_state["Entry unum function"]["Name"],
                "NextInput":"Scalar"
            }

            ir.append(config)
            ir = ir + next_state["unum IR"]

            return {
                "State Name": state_name,
                "unum IR": ir,
                "Entry unum function": config,
                "Exit unum function": next_state["Exit unum function"]
            }
        else:
            raise

    elif state["Type"] == "Map":

        iterator = translate_state_machine(state["Iterator"])
        global unum_map_counter
        unum_map = {
            "Name": f'UnumMap{unum_map_counter}',
            "Next": iterator["Entry unum function"]["Name"],
            "NextInput": "Map"
        }

        unum_map_sink = {
            "Name": f'UnumMapSink{unum_map_counter}'
        }
        unum_map_counter = unum_map_counter + 1

        iterator["Exit unum function"]["Next"] = unum_map_sink["Name"]
        iterator["Exit unum function"]["NextInput"] = {
            "Fan-in": {
                "Values": [
                    f'{iterator["Exit unum function"]["Name"]}-unumIndex-*'
                ]
            }
        }

        ir.append(unum_map)
        ir = ir + iterator["unum IR"]
        ir.append(unum_map_sink)

        if "Next" in state:
            next_state = _translate_state_machine(state["Next"], state_machine)
            unum_map_sink["Next"] = next_state["Entry unum function"]["Name"]
            unum_map_sink["NextInput"] = "Scalar"
            ir = ir + next_state["unum IR"]
            return {
                "State Name": state_name,
                "unum IR": ir,
                "Entry unum function": unum_map,
                "Exit unum function": next_state["Exit unum function"]
            }
        else:
            return {
                "State Name": state_name,
                "unum IR": ir,
                "Entry unum function": unum_map,
                "Exit unum function": unum_map_sink
            }


    elif state["Type"] == "Parallel":
        branches = [translate_state_machine(b) for b in state["Branches"]]
        global unum_parallel_counter
        unum_parallel = {
            "Name": f'UnumParallel{unum_parallel_counter}',
            "Next": [b["Entry unum function"]["Name"] for b in branches],
            "NextInput":"Scalar"
        }
        ir.append(unum_parallel)

        unum_parallel_sink = {
            "Name": f'UnumParallelSink{unum_parallel_counter}'
        }
        unum_parallel_counter = unum_parallel_counter +1
        parallel_fan_in_vals = [f'{branches[i]["Exit unum function"]["Name"]}-unumIndex-{i}' for i in range(len(branches))]
        for b in branches:
            ir = ir + b["unum IR"]
            b["Exit unum function"]["NextInput"] = {
                "Fan-in": {
                    "Values": parallel_fan_in_vals
                }
            }
            b["Exit unum function"]["Next"] = unum_parallel_sink["Name"]

        ir.append(unum_parallel_sink)

        if "Next" in state:
            next_state = _translate_state_machine(state["Next"], state_machine)
            unum_parallel_sink["Next"] = next_state["Entry unum function"]["Name"]
            unum_parallel_sink["NextInput"] = "Scalar"
            ir = ir + next_state["unum IR"]
            return {
                "State Name": state_name,
                "unum IR": ir,
                "Entry unum function": unum_parallel,
                "Exit unum function": next_state["Exit unum function"]
            }
        else:
            return {
                "State Name": state_name,
                "unum IR": ir,
                "Entry unum function": unum_parallel,
                "Exit unum function": unum_parallel_sink
            }


def translate_state_machine(state_machine):
    ''' Given a state machine, return its IR, entry state and end state

    A state machine = A Step Function state machine, a Map State, a Parallel
    State
    '''
    # entry_state = state_machine["States"][state_machine["StartAt"]]

    states = state_machine["States"]
    entry_state_name = state_machine["StartAt"]
    entry_state = states[entry_state_name]

    ret = _translate_state_machine(entry_state_name, state_machine)

    return ret


def main():
    parser = argparse.ArgumentParser(description='unmu frontend compiler for AWS Step Functions',
        # usage = "unum-cli [options] <command> <subcommand> [<subcommand> ...] [parameters]",
        #epilog="To see help text for a specific command, use unum-cli <command> -h"
        )
    parser.add_argument('-w', '--workflow',
        help="Step Functions state machine", required=True)
    parser.add_argument('-t', '--template',
        help="unum template", required=True)

    args = parser.parse_args()

    print(args.workflow)

    with open(args.workflow) as f:
        state_machine = json.loads(f.read())

    print(state_machine)
    ir = translate_state_machine(state_machine)


    # Add global configurations from unum-template.yaml
    with open(args.template) as f:
        template = load_yaml(f.read())

    for c in ir["unum IR"]:
        if "NextInput" in c and "Fan-in" in c["NextInput"]:
            c["Checkpoint"] = True
        else:
            c["Checkpoint"] = template["Globals"]["Checkpoint"]

    print("**************** IR ***************")
    print(f'{ir}')
if __name__ == '__main__':
    main()