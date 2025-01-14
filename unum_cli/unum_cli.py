#!/usr/bin/env python3
"""Unum command line interface

Unum command line for compiling, building and deploying applications.
"""

import json, os, sys, subprocess, time, yaml
try:
    from yaml import CLoader as Loader, CDumper as Dumper
except ImportError:
    from yaml import Loader, Dumper

from cfn_tools import load_yaml, dump_yaml

import logging
logger = logging.getLogger(__name__)

try:
    import coloredlogs
    coloredlogs.install(level='DEBUG', logger=logger, datefmt = '%H:%M:%S', fmt='[%(asctime)s] %(message)s')
except:
    logger.warning('`coloredlogs` do not exist. Revert to default logger.')
    pass


def download_github_directory(repo, github_dir, local_dir):
    '''Download the directory `github_dir` from `repo` into a local directory
    `local_dir`.

    If `github_dir` contains other directories, this function recursively
    downloads all child directories.

    Side effects: adding files and directories to the directory named
    `local_dir`
    '''
    import base64
    directory_contents = repo.get_contents(github_dir)

    for f in directory_contents:
        local_file_name = "/".join(f.path.split('/')[1:])

        if f.type == 'dir':
            os.makedirs(f'{local_dir}/{local_file_name}')
            download_github_directory(repo, f.path, local_dir)
        else:
            with open(f'{local_dir}/{local_file_name}', "wb") as file_out:
                file_text = base64.b64decode(f.content)
                file_out.write(file_text)

def get_github_directory_list(repo):
    '''Return a list of strings that are the names of directories in a github
    repository `repo`.

    No side effects
    '''
    contents = repo.get_contents("")
    app_list = [f.path for f in contents if f.type =="dir" ]

    return app_list

def sam_build_clean(args):
    if args.platform_template == None:
        # default AWS SAM template filename to template.yaml
        args.platform_template = 'template.yaml'

    try:
        with open(args.platform_template) as f:
            platform_template = load_yaml(f.read())
    except Exception as e:
        logger.error(f'\033[31m\n Build Clean Failed!\n\n Make sure a platform template file exists\033[0m')
        raise e

    # remove unum runtime files from each function's directory
    runtime_file_basename = os.listdir(".unum/runtime")
    for f in platform_template["Resources"]:
        if platform_template["Resources"][f]["Type"] != 'AWS::Serverless::Function':
            continue
        app_dir = platform_template["Resources"][f]["Properties"]["CodeUri"]
        runtime_files = [app_dir+e for e in runtime_file_basename]
        try:
            subprocess.run(['rm', '-f']+runtime_files, check=True)
        except Exception as e:
            raise e

    # remove the .aws-sam build directory
    try:
        ret = subprocess.run(["rm", "-rf", ".aws-sam"], check = True, capture_output=True)
    except Exception as e:
        raise e

def sam_build(platform_template, args):

    if args.clean:
        sam_build_clean(platform_template)
        return

    # copy files from runtime to each functions directory
    for f in platform_template["Resources"]:
        if platform_template["Resources"][f]["Type"] != 'AWS::Serverless::Function':
            continue
        app_dir = platform_template["Resources"][f]["Properties"]["CodeUri"]
        subprocess.run(f'cp .unum/runtime/* {app_dir}', shell=True, check=True)
        subprocess.run(f'cp .unum/{app_dir}unum_config.json {app_dir}', shell=True, check=True)

    # Add default requirements if requirements file does not exist or doesn't contains default dependencies
    default_dependencies = ["cfn-flip"]
    for f in platform_template["Resources"]:
        if platform_template["Resources"][f]["Type"] != 'AWS::Serverless::Function':
            continue
        app_dir = platform_template["Resources"][f]["Properties"]["CodeUri"]
        if os.path.isfile(f'{app_dir}requirements.txt'):
            with open(f'{app_dir}requirements.txt') as f:
                requirements = f.read().splitlines()
                if not set(default_dependencies).issubset(requirements):
                    with open(f'{app_dir}requirements.txt', 'a') as f:
                        f.write('\n')
                        f.write('\n'.join(default_dependencies))
        else:
            with open(f'{app_dir}requirements.txt', 'w') as f:
                f.write('\n'.join(default_dependencies))

    try:
        logger.debug("Running \"" + "sam build -t " + args.platform_template + " --use-container\"")
        ret = subprocess.run(["sam", "build", "-t", args.platform_template, "--use-container"],
            capture_output=True, check= True)
        logger.info(f'\033[32mBuild Succeeded\033[0m\n')
        logger.info(f'\033[33mBuilt Artifacts  : .aws-sam/build\033[0m')
        logger.info(f'\033[33mBuilt Template   : .aws-sam/build/template.yaml\033[0m\n')
        logger.info(f'\033[33mCommands you can use next\n=========================\033[0m')
        logger.info(f'\033[33m[*] Deploy: unum_cli deploy\033[0m\n')
    except subprocess.CalledProcessError as e:
        logger.error(f'\033[31m \n Build Failed!\n\n AWS SAM failed to build due to:')
        # TODO: Improve error message
        logger.error(e.stderr)
        # raise e

def sam_template_generate(unum_template):
    ''' Given an unum template, return an AWS SAM template as a python dict

        @param unum_template python dict

        @return sam_template python dict
    '''

    # boilerplate SAM template fields
    sam_template = {"AWSTemplateFormatVersion": '2010-09-09',
                    "Transform": "AWS::Serverless-2016-10-31"}

    # save workflow-wide configurations as environment variables.
    # Globals:
    #   Function:
    #       Environment:
    #           Variables:
    # These variables will be accessible by Lambda code as environment variables.
    sam_template["Globals"] = {
            "Function": {
                "Environment": {
                    "Variables":{
                        "UNUM_INTERMEDIARY_DATASTORE_TYPE": unum_template["Globals"]["UnumIntermediaryDataStoreType"],
                        "UNUM_INTERMEDIARY_DATASTORE_NAME": unum_template["Globals"]["UnumIntermediaryDataStoreName"],
                        "FAAS_PLATFORM": unum_template["Globals"]["FaaSPlatform"],
                        "CHECKPOINT":unum_template["Globals"]["Checkpoint"],
                        "GC":unum_template["Globals"]["GC"]
                    }
                }
            }
        }
    # Set all Lambda timeouts to 900 sec
    sam_template["Globals"]["Function"]["Timeout"] = 900

    # Copy other global settings from unum-template to sam template
    if "MemorySize" in unum_template["Globals"]:
        sam_template["Globals"]["Function"]["MemorySize"] = unum_template["Globals"]["MemorySize"]

    # For each unum function, create a AWS::Serverless::Function resource in
    # the SAM template under the "Resources" field.
    # All unum functions "Handler" is wrapper.lambda_handler
    # Copy over "CodeUri", "Runtime"
    # Add 
    #   + "AmazonDynamoDBFullAccess"
    #   + "AmazonS3FullAccess"
    #   + "AWSLambdaRole"
    #   + "AWSLambdaBasicExecutionRole"
    # if any is not listed already in the unum template
    unum_function_needed_policies = ["AmazonDynamoDBFullAccess","AmazonS3FullAccess","AWSLambdaRole","AWSLambdaBasicExecutionRole"]
    sam_template["Resources"]={}
    sam_template["Outputs"] = {}

    for f in unum_template["Functions"]:
        unum_function_policies = []
        if "Policies" in unum_template["Functions"][f]["Properties"]:
            unum_function_policies = unum_template["Functions"][f]["Properties"]["Policies"]

        sam_template["Resources"][f'{f}Function'] = {
                "Type":"AWS::Serverless::Function",
                "Properties": {
                    "Handler":"main.lambda_handler",
                    "Runtime": unum_template["Functions"][f]["Properties"]["Runtime"],
                    "CodeUri": unum_template["Functions"][f]["Properties"]["CodeUri"],
                    "Policies": list(set(unum_function_needed_policies) | set(unum_function_policies))
                },
            }

        # Add command to acquired the deployed Lambda's ARN to the "Outputs"
        # fields of the SAM template
        arn = f"!GetAtt {f}Function.Arn"
        sam_template["Outputs"][f'{f}Function'] = {"Value": f"!GetAtt {f}Function.Arn"}

    # If using DynamoDB as the intermediary datastore, add the DynamoDB table to the SAM template
    if unum_template["Globals"]["UnumIntermediaryDataStoreType"] == "dynamodb":
        sam_template["Resources"][f'{unum_template["Globals"]["UnumIntermediaryDataStoreName"]}Table'] = {
                "Type": "AWS::DynamoDB::Table",
                "Properties": {
                    "TableName": unum_template["Globals"]["UnumIntermediaryDataStoreName"],
                    "AttributeDefinitions": [
                        {
                            "AttributeName": "Name",
                            "AttributeType": "S"
                        }
                    ],
                    "KeySchema": [
                        {
                            "AttributeName": "Name",
                            "KeyType": "HASH"
                        }
                    ],
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5
                    }
                }
            }

    return sam_template

def deploy_sam_first():
    # Deploy the functions as is, get each function's arn, update each
    # function's unum_config.json with the arn, store function name to arn
    # mapping in function-arn.yaml

    # First deployment. Deploy functions as is
    with open("unum-template.yaml") as f:
        app_template = yaml.load(f.read(),Loader=Loader)

    app_name = app_template["Globals"]["ApplicationName"]

    try:
        ret = subprocess.run(["sam", "deploy",
                          "--stack-name", app_name,
                          "--region", "us-west-1",
                          "--no-fail-on-empty-changeset",
                          "--no-confirm-changeset",
                          "--resolve-s3",
                          "--capabilities",
                          "CAPABILITY_IAM"],
                          capture_output=True)
    except Exception as e:
        raise e

    # grep for the functions' arn
    stdout = ret.stdout.decode("utf-8")
    logger.info(stdout)
    logger.info(ret.stderr.decode("utf-8"))
    try:
        deploy_output = stdout.split("Outputs")[1]
    except:
        raise IOError(f'SAM stack with the same name already exists')
    
    deploy_output = deploy_output.split('-------------------------------------------------------------------------------------------------')[1]
    
    deploy_output = deploy_output.split()
    function_to_arn_mapping = {}

    i = 0
    while True:
        while deploy_output[i] != "Key":
            i = i+1

        function_name = deploy_output[i+1].replace("Function","")

        while deploy_output[i] != "Value":
            i = i+1
        function_arn = deploy_output[i+1] + deploy_output[i+2]
        function_to_arn_mapping[function_name] = function_arn

        if len(app_template["Functions"]) == len(function_to_arn_mapping.keys()):
            break

    # store function name to arn mapping in function-arn.yaml
    with open("function-arn.yaml", 'w') as f:
        d = yaml.dump(function_to_arn_mapping, Dumper=Dumper)
        f.write(d)

    logger.info(f'function-arn.yaml created')

    # update each function's unum_config.json by replacing function names with
    # arns in the continuation
    for f in app_template["Functions"]:
        app_dir = app_template["Functions"][f]["Properties"]["CodeUri"]
        logger.info(f'Updating function {f} in {app_dir}')

        with open(f'{app_dir}unum_config.json', 'r+') as c:
            config = json.loads(c.read())
            logger.info(f'Overwriting {app_dir}unum_config.json')
            if "Next" in config:
                if isinstance(config["Next"],dict):
                    config["Next"]["Name"] = function_to_arn_mapping[config["Next"]["Name"]]
                if isinstance(config["Next"], list):
                    for cnt in config["Next"]:
                        cnt["Name"] = function_to_arn_mapping[cnt["Name"]]
                c.seek(0)
                c.write(json.dumps(config, indent=4))
                c.truncate()
                logger.info(f'{app_dir}unum_config.json Updated')


def create_function_arn_mapping(sam_stdout, unum_template):
    ''' create a functino-arn.yaml file and return the mapping as dict
    '''
    # grep for the functions' arn This method relies on string processing sam
    # deploy stdout to get Lambda ARNs. The obvious downside is that if sam
    # deploy's output format changes, the following code won't work. Still
    # looking for a more reliable/programmable way to get this information.

    try:
        deploy_output = sam_stdout.split("Outputs")[1]
    except:
        logger.error(f'Failed to create function-arn.yaml.')
        logger.error(f'SAM stack with the same name already exists')
        raise IOError(f'SAM stack with the same name already exists')
    
    deploy_output = deploy_output.split('-------------------------------------------------------------------------------------------------')[1]
    
    deploy_output = deploy_output.split()
    function_to_arn_mapping = {}

    i = 0
    while True:
        while deploy_output[i] != "Key":
            i = i+1

        function_name = deploy_output[i+1].replace("Function","")

        while deploy_output[i] != "Value":
            i = i+1
        function_arn = deploy_output[i+1] + deploy_output[i+2]
        function_to_arn_mapping[function_name] = function_arn

        if len(unum_template["Functions"]) == len(function_to_arn_mapping.keys()):
            break

    # store function name to arn mapping in function-arn.yaml
    with open("function-arn.yaml", 'w') as f:
        d = yaml.dump(function_to_arn_mapping, Dumper=Dumper)
        f.write(d)

    logger.info(f'function-arn.yaml Created')

    return function_to_arn_mapping

def update_unum_config_continuation_to_arn(platform_template, function_to_arn_mapping):
    ''' Given a workflow and its function-to-arn mapping, update each
    function's continuation in unum_config.json with the Lambda's ARN

    This function changes the unum_config.json files in the build artifacts,
    i.e., .aws-sam. It does not modify the source code.
    '''
    base_dir = f'.aws-sam/build'
    for f in platform_template["Resources"]:

        if platform_template["Resources"][f]["Type"] == 'AWS::Serverless::Function':
            function_artifact_dir = f'{base_dir}/{f}'

            logger.info(f'[*] Updating unun_config.json in {function_artifact_dir}')

            try:
                c = open(f'{function_artifact_dir}/unum_config.json', 'r+')
                config = json.loads(c.read())
                logger.info(f'Current config: {config}')

                if "Next" in config:
                    if isinstance(config["Next"],dict):
                        config["Next"]["Name"] = function_to_arn_mapping[config["Next"]["Name"]]
                    if isinstance(config["Next"], list):
                        for cnt in config["Next"]:
                            cnt["Name"] = function_to_arn_mapping[cnt["Name"]]
                    c.seek(0)
                    c.write(json.dumps(config, indent=4))
                    c.truncate()

                logger.info(f'\033[32m {function_artifact_dir}/unum_config.json Updated\033[0m')
                c.close()

            except Exception as e:
                logger.error(f'\033[31m Exceptions updating {function_artifact_dir}/unum_config.json:\033[0m')
                logger.error(f'\033[31m {e} \033[0m')
                return False

    return True



def deploy_sam(args):
    import shutil

    # check if AWS_PROFILE is set
    if os.getenv("AWS_PROFILE") == None:
        logger.error(f'\033[31m \n Deploy Failed!\n\n Make sure AWS_PROFILE is set\033[0m')
        raise OSError(f'Environment variable $AWS_PROFILE must exist')

    # read unum template file
    try:
        with open(args.template) as f:
            unum_template = load_yaml(f.read())
            stack_name = unum_template["Globals"]["ApplicationName"]
    except Exception as e:
        logger.error(f'\033[31m \n Deploy Failed!\n\n Failed to find unum template file: {args.template}\033[0m\n')
        logger.error(f'\033[31m Make sure the unum template file exists\033[0m')
        logger.error(f'\033[31m You can specify a platform template file with -t/--template\033[0m')
        logger.error(f'\033[31m See unum_cli deploy -h for more details\033[0m\n')
        raise e

    # read platform template file (i.e., sam template)
    try:
        with open(args.platform_template) as f:
            platform_template = load_yaml(f.read())
    except Exception as e:
        logger.error(f'\033[31m \n Deploy Failed!\n\n Failed to find platform template file: {args.platform_template}\033[0m\n')
        logger.error(f'\033[31m Make sure the platform template file exists\033[0m')
        logger.error(f'\033[31m You can specify a platform template file with -s/--platform_template\033[0m')
        logger.error(f'\033[31m See unum_cli deploy -h for more details\033[0m\n')
        raise e



    def rollback_first_deployment():
        logger.info(f'\033[31mRemoving function-arn.yaml\033[0m\n')
        if os.path.isfile('function-arn.yaml'):
            try:
                ret = subprocess.run(["rm", "function-arn.yaml"], check = True, capture_output=True)
            except Exception as e:
                logger.error(f'Failed to delete function-arn.yaml')


        # check if the stack is deployed
        ret = subprocess.run(["aws", "cloudformation", "describe-stacks",
                      "--stack-name", stack_name],
                      capture_output=True)

        if ret.returncode == 0:
            stack_info = json.loads(ret.stdout.decode("utf-8"))

            if "Stacks" in stack_info and len(stack_info["Stacks"])>0:
                # if stack indeed exists on AWS, delete it
                logger.info(f'\033[31mRolling back trial deployment\033[0m\n')
                ret = subprocess.run(["aws", "cloudformation", "delete-stack",
                              "--stack-name", stack_name],
                              capture_output=True)
                if ret.returncode != 0:
                    logger.error(f'\033[31mFailed to delete AWS stack {stack_name}\033[0m')

    first_deploy = False
    if os.path.isfile('function-arn.yaml') == False:
        # Need to do a trial deployment to create the Lambda resources and get
        # their arn. With the arns, replace the `Name` field of the
        # continuation of each unum_config.json with the arn of the deployed
        # Lambda, and then deploy again. Note that we modify the
        # unum_config.json in the build artifacts not the source code.

        first_deploy = True

        # trial deployment
        ret, sam_output = sam_deploy_wrapper(stack_name)
        if ret == False:
            logger.error(f'\033[31m Trial Deployment Failed!\033[0m\n')
            raise OSError(f'Failed to deploy to AWS')

        logger.info(sam_output)
        logger.info(f'\033[32m Lambda resources created\033[0m\n')
        logger.info(f'Creating function-to-arn mapping ......')
        # create the function to arn mapping
        function_to_arn_mapping = create_function_arn_mapping(sam_output, unum_template)
        logger.info(f'\033[32m\n Function-to-arn Mapping Created\033[0m\n')

    # copy function-arn.yaml into .aws-sam/build/[function_name]/
    base_dir = f'.aws-sam/build'

    for f in platform_template["Resources"]:
        if platform_template["Resources"][f]["Type"] == 'AWS::Serverless::Function':
            function_artifact_dir = f'{base_dir}/{f}'

            logger.info(f'[*] Copying function-arn.yaml into {function_artifact_dir}')

            shutil.copy('function-arn.yaml', function_artifact_dir)


        # # update the unum_config.json in all functions (in the build artifacts, not source code)
        # print(f'Updating unum configuration ......')
        # if update_unum_config_continuation_to_arn(platform_template, function_to_arn_mapping) == False:

        #     # If updating unum configuration fails at this point, rollback
        #     print(f'\033[31m\nFailed to update unum configuration\033[0m\n')
        #     rollback_first_deployment()
        #     print(f'\033[31m\nTrial deployment rolled back\033[0m\n')
        #     print(f'\033[31m\nDeployment Failed\033[0m\n')
        #     exit(1)

        # print(f'\033[32m \nAll unum configuration updated\033[0m\n')
        # time.sleep(5)

    # Validate build artifacts first
    # User might have run unum_cli build asynchronously.
    # Additionally, we need to make sure that all unum configurations have
    # ARNs in their continuations, not function names
    if validate_sam_build_artifacts(platform_template) == False:
        logger.error(f'\033[31m \n Deploy Failed!\n\n Invalid build artifacts\033[0m\n')

        if first_deploy:
            # rollback if this is the first time deploying
            rollback_first_deployment()

        raise ValueError(f'Invalid build artifacts')

    # if validate_sam_build_artifacts_unum_config() == False:
    #     if os.path.isfile('function-arn.yaml'):
    #         with open('function-arn.yaml') as f:
    #             function_to_arn_mapping = load_yaml(f.read())

            # update_unum_config_continuation_to_arn(platform_template, function_to_arn_mapping)

    #     else:
    #         print(f'\033[31m \nDeploy Failed!\n\n Invalid build artifacts\033[0m\n')
    #         print(f'\033[31m unum configurations do not contain ARNs and function-arn.yaml does not exist.\033[0m\n')
    #         if first_deploy:
    #             # rollback if this is the first time deploying
    #             rollback_first_deployment()

    #         raise ValueError(f'Invalid build artifacts')


    # deploy
    ret, sam_output = sam_deploy_wrapper(stack_name)
    if ret == False:
        logger.error(f'\033[31m Deploy Failed!\033[0m\n')
        raise OSError(f'Failed to deploy to AWS')
    else:
        logger.info(sam_output)
        logger.info(f'\033[32m\nDeploy Succeeded!\033[0m\n')
        logger.info(f'\033[33mCommands you can use next\n=========================\033[0m')
        logger.info(f'\033[33m[*] Deploy: unum_cli invoke\033[0m\n')

def sam_deploy_wrapper(stack_name):
    ''' Wrapper around a sam deploy subprocess Note that unum_cli deploy will
    always use .aws-sam/build/template.yaml as the sam deploy template (i.e.,
    sam deploy -t .aws-sam/build/template.yaml), because unum_cli piggybacks
    on the sam build artifacts
    '''
    ret = subprocess.run(["sam", "deploy",
                          "--stack-name", stack_name,
                          "--template-file", ".aws-sam/build/template.yaml",
                          "--no-fail-on-empty-changeset",
                          "--no-confirm-changeset",
                          "--resolve-s3",
                          "--capabilities",
                          "CAPABILITY_IAM"],
                          capture_output=True)

    if ret.returncode != 0:
        logger.error(f'\033[31msam deploy Failed! Error message from sam:\033[0m')
        logger.error(f'\033[31m {ret.stderr.decode("utf-8")} \033[0m')
        return False, ret.stderr.decode("utf-8")

    logger.info(f'\033[32m\nsam deploy Succeeded\033[0m')
    return True, ret.stdout.decode("utf-8")

def validate_sam_build_artifacts_unum_config():
    ''' Making sure continuations in unum_config.json have ARns.

    Check all function artifacts in .aws-sam.
    '''
    built_functions = [d for d in os.listdir('.aws-sam/build') if d.endswith('Function')]
    for f in built_functions:
        try:
            with open(f'.aws-sam/build/{f}/unum_config.json') as c:
                config = json.loads(c.read())
        except Exception as e:
            logger.error(f'\033[31m .aws-sam/build/{f}/unum_config.json failed to open \033[0m')
            raise e

        if "Next" in config:
            if isinstance(config["Next"],dict):
                if config["Next"]["Name"].startswith('arn:aws:lambda')== False:
                    return False
            if isinstance(config["Next"], list):
                for cnt in config["Next"]:
                    if cnt["Name"].startswith('arn:aws:lambda') == False:
                        return False

    return True

def validate_sam_build_artifacts(platform_template):
    '''
    AWS: check if all functions in the .aws-sam directory has a unum-config.json file
    '''

    def check_subset(l1, l2):
        ''' return if l1 is a subset of l2
        Return True if all elements of l1 are in l2. Otherwise False
        '''
        for e in l1:
            if e not in l2:
                return False
        return True
    
    # check if .aws-sam/ and .aws-sam/build exists
    if os.path.isdir('.aws-sam/build') == False:
        logger.warning(f'\033[31m \n No build artifacts detected\033[0m\n')
        logger.warning('''\033[31m For AWS deployment, make sure you have the build artifacts under .aws-sam/build.
 To build an unum workflow, use the unum_cli build command.
 See unum_cli build -h for more details.\033[0m''')
        return False

    # check if the number of directories under .aws-sam/build/ match the
    # number of functions in platform_template
    # Functions should have a directory that ends with the word 'Function', e.g., HelloFunction.
    built_functions = [d for d in os.listdir('.aws-sam/build') if d.endswith('Function')]
    logger.info(f'Built functions detected:')
    for f in built_functions:
        logger.info(f' [*] {f}')

    template_resources = platform_template['Resources'].keys()
    if check_subset(built_functions, template_resources) == False:
        logger.warning(f'\033[31m \n Function artifacts do not match the template\033[0m\n')
        return False

    # check if each function directory in .aws-sam/build/ has
    #    + app.py
    #    + unum.py
    #    + ds.py
    #    + unum_config.json
    expected_files_list = [
        'app.py',
        'unum.py',
        'ds.py',
        'unum_config.json'
    ]

    for f in built_functions:
        logger.info(f'Checking {f} ......')
        if check_subset(expected_files_list, os.listdir(f'.aws-sam/build/{f}')):
            logger.info(f'\033[32m Success\033[0m')
        else:
            logger.error(f'\033[31m Failed!\033[0m')
            logger.error(f"\033[31m Make sure you have the following files in each function's build directory:\033[0m")
            logger.error(f'\033[31m {expected_files_list}\033[0m')
            return False

    return True

def validate_build_artifacts(platform_template, platform):

    if platform == 'aws':
        return validate_sam_build_artifacts(platform_template)
    else:
        raise OSError(f'Only AWS SAM deployment supported')

    pass


def invoke_sam(args):
    import shutil

    # check if AWS_PROFILE is set
    if os.getenv("AWS_PROFILE") == None:
        logger.error(f'\033[31m \n Invoke Failed!\n\n Make sure AWS_PROFILE is set\033[0m')
        raise OSError(f'Environment variable $AWS_PROFILE must exist')

    # read unum template file
    try:
        with open(args.template) as f:
            unum_template = load_yaml(f.read())
            stack_name = unum_template["Globals"]["ApplicationName"]
    except Exception as e:
        logger.error(f'\033[31m \n Invoke Failed!\n\n Failed to find unum template file: {args.template}\033[0m\n')
        logger.error(f'\033[31m Make sure the unum template file exists\033[0m')
        logger.error(f'\033[31m You can specify a platform template file with -t/--template\033[0m')
        logger.error(f'\033[31m See unum_cli invoke -h for more details\033[0m\n')
        raise e

    # select the function to invoke finding the Start function on the unum template
    start_function = None
    for f in unum_template["Functions"]:
        if "Start" in unum_template["Functions"][f]["Properties"] and unum_template["Functions"][f]["Properties"]["Start"] == True:
            start_function = f + "Function"
            break

    if start_function == None:
        logger.error(f'\033[31m \n Invoke Failed!\n\n Failed to find the Start function on the unum template file: {args.template}\033[0m\n')
        logger.error(f'\033[31m Make sure the unum template file has a Start function\033[0m')
        raise ValueError(f'Failed to find the Start function on the unum template')

    # invoke start function
    ret, sam_output = sam_invoke_wrapper(start_function, stack_name)
    if ret == False:
        logger.error(f'\033[31m Invoke Failed!\033[0m\n')
        raise OSError(f'Failed to invoke to AWS')
    else:
        logger.info(sam_output)
        logger.info(f'\033[32m\Invoke Succeeded!\033[0m\n')
        logger.info(f'Checking if there are finish functions ......')

    # if unum have finish functions, wait for them to finish
    finish_functions = []
    for f in unum_template["Functions"]:
        if "Finish" in unum_template["Functions"][f]["Properties"] and unum_template["Functions"][f]["Properties"]["Finish"] == True:
            finish_functions.append({ "Name": f, "Return": None })
            logger.info(f'- {f} function')

    if len(finish_functions) == 0:
        logger.info(f'\033[33m\nNo finish functions found\033[0m')
        logger.info(f'\033[32m\nInvoke finished\033[0m')
        return
    
    logger.info(f'\nWaiting for the workflow to finish ......')
    if unum_template["Globals"]["UnumIntermediaryDataStoreType"] == "dynamodb":
        for f in finish_functions:
            import boto3
            dynamodb = boto3.resource('dynamodb')
            session_id = json.loads(sam_output)[1]
            function_name = session_id + "/" + f["Name"] + "-output"
            table_name = unum_template["Globals"]["UnumIntermediaryDataStoreName"]
            table = dynamodb.Table(table_name)
            while True:
                response = table.get_item(Key={'Name': function_name})
                if "Item" in response:
                    f["return"] = response["Item"]["User"]
                    break
                time.sleep(5)
            logger.info(f'- {f["Name"]} function : {f["return"]}')
    else:
        logger.info(f'\033[33m\nOnly DynamoDB is supported as the intermediary datastore\033[0m')
        logger.info(f'\033[32m\nInvoke finished\033[0m')
        return

    logger.info(f'\033[32m\nInvoke finished\033[0m')


def sam_invoke_wrapper(function_name, stack_name):
    ''' Wrapper around a sam invoke subprocess.
    '''
    ret = subprocess.run(["sam", "remote", "invoke",
                          function_name,
                          "--stack-name", stack_name],
                          capture_output=True)

    if ret.returncode != 0:
        logger.error(f'\033[31msam invoke Failed! Error message from sam:\033[0m')
        logger.error(f'\033[31m {ret.stderr.decode("utf-8")} \033[0m')
        return False, ret.stderr.decode("utf-8")

    logger.info(f'\033[32m\nsam invoke Succeeded\033[0m')
    return True, ret.stdout.decode("utf-8")


def unum_init(args):
    import shutil

    app_name = args.name

    # create the {app_name} directory under the current directory
    try:
        os.makedirs(app_name)
    except FileExistsError:
        logger.error(f'`{app_name}` directory already exists')
        sys.exit(1)
    except Exception as e:
        logger.error(f'Failed to create `{app_name}` directory due to {e}')
        sys.exit(1)

    # create {app_name}/.unum directory, download the Unum runtime into
    # {app_name}/.unum/runtime from Unum's github repo
    os.makedirs(f'{app_name}/.unum')
    os.makedirs(f'{app_name}/.unum/runtime')

    # Copy files from the runtime folder to .unum/runtime folder
    runtime_folder = os.path.join(os.path.dirname(__file__), '..', 'runtime')
    for f in os.listdir(runtime_folder):
        shutil.copy(os.path.join(runtime_folder, f), f'{app_name}/.unum/runtime')


    # download the application starter files into {app_name} directory
    # from the Unum appstore github repo
    try:
        from github import Github
        git = Github()

        unum_app_repo = git.get_repo("MateusBMP/unum-appstore")
    except Exception as e:
        logger.error(f'Cannot access Unum appstore')
        logger.error(e)
        logger.warning(f'Continue without starter application files')
        logger.debug(f'{app_name} created')
        sys.exit(1)

    starter_app = "hello-world"
    if args.template:
        try:
            template_list = get_github_directory_list(unum_app_repo)

        except Exception as e:
            logger.error(f'Failed to get the list of starter apps from Unum appstore')
            logger.error(e)
            logger.warning(f'Continue initialization with the default template')

        else:
            logger.info('Which app template do you want to start with:')
            for i, t in enumerate(template_list):
                logger.info(f'    {i}. {t}')
            s = int(input('Type your number: '))

            try:
                starter_app = template_list[s]
            except IndexError as e:
                logger.error('Invalid number. Continue initialization with the default template')
                starter_app = "hello-world"

    try:
        logger.info(f'Downloading starter template `{starter_app}`')
        download_github_directory(unum_app_repo, starter_app, app_name)

        logger.debug(f'Template `{starter_app}` downloaded')

        logger.debug(f'{app_name} created')

    except Exception as e:
        logger.error(f'Failed to download {starter_app}')
        logger.error(e)
        logger.warning(f'Continue without starter application files')
        logger.debug(f'{app_name} created')

def unum_compile(args):
    '''Compile from frontend definition to Unum IR

    Supported frontends:
        1. AWS Step Functions (Amazon State Language)

    This function handles creating the related files inside .unum/ while the
    frontend module are purely functional
    '''

    # read from args.unum_template if some options are not specified on
    # the command line.
    if args.unum_template == None or args.workflow_type == None or args.workflow_definition == None:

        if args.unum_template == None:
            logger.warning('"UnumTemplate" not defined. Default to unum-template.yaml')
            args.unum_template = "unum-template.yaml"

        try:
            with open(args.unum_template) as tf:
                app_template = load_yaml(tf.read())
                # print(app_template)
        except Exception as e:
            logger.error(f'{args.unum_template} does not exist')
            exit(1)

        try:
            if args.workflow_definition == None:
                args.workflow_definition = app_template['Globals']['WorkflowDefinition']
        except KeyError as e:
            logger.error('"WorkflowDefinition" not defined')
            exit(1)

        try:
            if args.workflow_type == None:
                args.workflow_type = app_template['Globals']['WorkflowType']
        except KeyError as e:
            logger.error('"WorkflowType" not defined')
            exit(1)

    # print(args)

    # import the right frontend compiler based on args.workflow_type.
    # pass the content of workflow definition and unum-template as is to the frontend compiler
    if args.workflow_type == 'step-functions':
        from frontend import step_functions as fc

        try:
            with open(args.workflow_definition) as f:
                state_machine = json.loads(f.read())
        except Exception as e:
            logger.error(f'Could not load Step Functions workflow definition: {args.workflow_definition}')
            raise e

        try:
            with open(args.unum_template) as f:
                app_template = load_yaml(f.read())
        except Exception as e:
            logger.error(f'Could not load Unum template {args.unum_template}')
            raise e

        ir = fc.compile(state_machine, app_template, args.optimize)
        # print(ir)

        # Save generated IR into .unum/
        update_template = False
        for f in ir['unum IR']:
            if f['Name'] in app_template['Functions']:
                function_dir = f".unum/{app_template['Functions'][f['Name']]['Properties']['CodeUri']}"
            else:
                function_dir = f".unum/{f['Name']}"
                update_template = True

            try:
                # print(function_dir)
                os.makedirs(function_dir)
            except FileExistsError as e:
                pass
            except Exception as e:
                logger.error(f'Could not create {function_dir}')

            try:
                with open(os.path.join(function_dir, 'unum_config.json'), 'w') as cf:
                    cf.write(json.dumps(f, indent=4))
            except Exception as e:
                raise e
        
        # if additional functions were generated, update the unum-template file
        if update_template:
            pass

        # save the template file after IR compilation into .unum/ always

        logger.debug('Unum IR generated from Step Functions')


    elif args.workflow_type == 'azure':
        pass
    else:
        raise IOError(f'Unknown WorkflowType: {args.workflow_type}')

def unum_build(args):
    if args.clean:
        if args.platform == 'aws':
            sam_build_clean(args)
        elif args.platform == None:
            sam_build_clean(args)
        elif args.platform == 'azure':
            pass
        else:
            pass
        return

    if args.generate:
        logger.info("\033[33mGenerating platform template...........\033[0m\n")
        unum_template(args)

    if args.platform == None:
        logger.info(f'No target platform specified.\nDefault to \033[33m\033[1mAWS\033[0m.')
        logger.info(f'If AWS is not the desirable target, specify a target platform with -p or --platform.\nSee unum_cli build -h for details.\n')
        args.platform='aws'

    if args.platform == 'aws':
        # Default to AWS
        if args.platform_template == None:
            logger.info(f'No platform template file specified.\nDefault to\033[33m\033[1m template.yaml \033[0m')
            logger.info(f'You can specify a platform template file with -s or --platform_template.\nSee unum_cli build -h for details.\n')
            args.platform_template = "template.yaml"

        try:
            with open(args.platform_template) as f:
                platform_template = load_yaml(f.read())
        except Exception as e:
            logger.error(f'\033[31m \n Build Failed!\n\n Make sure the platform template file exists\033[0m')
            logger.error(f'\033[31m You can specify a platform template file with -s/--platform_template\033[0m')
            logger.error(f'\033[31m Or generate a platform template from your unum template with "unum_cli template" or "unum_cli build -g"\033[0m')
            logger.error(f'\033[31m See unum_cli -h for more details\033[0m\n')
            raise e

        sam_build(platform_template, args)
    else:
        pass

def unum_template(args):
    # unum_cli template -c/--clean
    if args.clean:
        try:
            subprocess.run(['rm', '-f', 'template.yaml'], check=True)
        except Exception as e:
            raise e
        return

    # if platform is not specified
    if args.platform == None:
        logger.info(f'No target platform specified.\nDefault to \033[33m\033[1mAWS\033[0m.')
        logger.info(f'If AWS is not the desirable target, specify a target platform with -p or --platform.\nSee unum_cli template -h for details.\n')
        args.platform='aws'

    # if a unum-template file is not specified
    if args.template == None:
        logger.info(f'No unum template file specified.\nDefault to\033[33m\033[1m unum-template.yaml \033[0m')
        logger.info(f'You can specify a template file with -t or --template.\nSee unum_cli template -h for details.\n')
        args.template = 'unum-template.yaml'

    try:
        with open(args.template) as f:
            unum_template = yaml.load(f.read(), Loader=Loader)
    except Exception as e:
        logger.error(f'\033[31m \n Build Failed!\n\n Make sure the template file exists\033[0m')
        raise e

    if args.platform == 'aws':
        platform_template = sam_template_generate(unum_template)

        # Save the AWS SAM template as 'template.yaml'
        logger.info(f'\033[32mPlatform Template Generation Succeeded\033[0m\n')
        logger.info(f'\033[33mAWS SAM Template: template.yaml\033[0m\n')
        try:
            with open('template.yaml','w') as f:
                f.write(dump_yaml(platform_template))
        except Exception as e:
            raise e

        # AWS-specific template post-processing
        # YAML dumpper (even the AWS-provided one) doesn't correctly recognize
        # Cloudformation tags and results in !GetAtt being saved as a string.
        with open('template.yaml','r+') as f:
            cnt = f.read()
            # YAML dumpper (even the AWS-provided one) doesn't correctly recognize
            # Cloudformation tags and results in !GetAtt being saved as a string.
            cnt = cnt.replace("Value: '!GetAtt", "Value: !GetAtt").replace("Function.Arn'","Function.Arn")
            f.seek(0)
            f.write(cnt)
            f.truncate()

    elif args.platform == 'azure':
        # platform_template = generate_azure_template(app_template)
        return
    elif args.platform ==None:
        logger.error(f'Failed to generate platform template due to missing target')
        raise ValueError(f'Specify target platform with -p or --platform. See unum_cli template -h for details.')
    else:
        raise ValueError(f'Unknown platform: {args.platform}')

def unum_deploy(args):

    # Make sure args has all names with valid values
    if args.platform_template == None:
        # platform_template not specified, default to AWS template.yaml
        logger.info(f'No platform template file specified.\nDefault to\033[33m\033[1m template.yaml \033[0m\n')
        args.platform_template = 'template.yaml'

    if args.template == None:
        # unum template not specified, default to unum-template.yaml
        logger.info(f'No unum template file specified.\nDefault to\033[33m\033[1m unum-template.yaml \033[0m\n')
        args.template = 'unum-template.yaml'

    try:
        with open(args.platform_template) as f:
            platform_template = load_yaml(f.read())
    except Exception as e:
        logger.error(f'\033[31m \n Deploy Failed!\n\n Failed to find platform template file: {args.platform_template}\033[0m\n')
        logger.error(f'\033[31m Make sure the platform template file exists\033[0m')
        logger.error(f'\033[31m You can specify a platform template file with -s/--platform_template\033[0m')
        logger.error(f'\033[31m Or generate a platform template from your unum template with "unum_cli template" or "unum_cli build -g"\033[0m')
        logger.error(f'\033[31m See unum_cli -h for more details\033[0m\n')
        raise e

    if "AWSTemplateFormatVersion" in platform_template:
        if args.platform == None:
            # platform not specified
            args.platform = 'aws'

        # platform specified, make sure that it's the same as the platform_template
        elif args.platform =='aws':
            pass
        else: 
            logger.error(f'\033[31m \n Deploy Failed!\n\n Specified platform failed to match template\033[0m\n')
            logger.error(f'\033[31m Specified platform: {args.platform}, template: aws.\033[0m\n')
            raise ValueError(f'Specified platform failed to match template')

    elif "AZure" in platform_template:
        raise OSError(f'AZure deployment not supported yet')
    else:
        raise OSError(f'Other deployment not supported yet')
    
    # build if -b option
    if args.build:
        args.clean=False
        args.generate=False
        sam_build(args)
    
    # validates build artifacts before deploying
    if validate_build_artifacts(platform_template, args.platform) == False:
        logger.error(f'\033[31m \n Deploy Failed!\n\n Invalid build artifacts\033[0m\n')
        raise ValueError(f'Invalid build artifacts')

    logger.info(f'\033[32m\nBuild artifacts validation passed\033[0m\n')

    if args.platform == 'aws':
        logger.info(f'\033[33m\033[1mDeploying to AWS ......\033[0m\n')
        deploy_sam(args)
    elif args.platform == 'azure':
        raise OSError(f'AZure deployment not supported yet')
    else:
        raise OSError(f'Other deployment not supported yet')


def unum_invoke(args):
    if args.template == None:
        # unum template not specified, default to unum-template.yaml
        logger.info(f'No unum template file specified.\nDefault to\033[33m\033[1m unum-template.yaml \033[0m\n')
        args.template = 'unum-template.yaml'

    if args.platform == None:
        # platform not specified
        logger.info(f'No target platform specified.\nDefault to \033[33m\033[1mAWS\033[0m.')
        args.platform = 'aws'

    if args.platform == 'aws':
        logger.info(f'\033[33m\033[1mInvoke to AWS ......\033[0m\n')
        invoke_sam(args)
    elif args.platform == 'azure':
        raise OSError(f'AZure invoke not supported yet')
    else:
        raise OSError(f'Other invoke not supported yet')


def main():
    import argparse

    parser = argparse.ArgumentParser(description='Unum CLI for creating, building and deploying Unum applications',
        usage = "unum_cli [options] <command>",
        epilog="To see help text for a specific command, use unum_cli <command> -h")

    subparsers = parser.add_subparsers(title='command', dest="command", required=True)

    # init command parser
    init_parser = subparsers.add_parser("init", description="initialize a Unum application")
    init_parser.add_argument('-n', '--name', required=True, help='application name')
    init_parser.add_argument('-t', '--template', action="store_true", help="initialize with an application template")

    # compile commmand parser
    compile_parser = subparsers.add_parser("compile", description="compile an application to Unum IR")
    compile_parser.add_argument('-t', '--workflow-type', required=False, help="workflow type")
    compile_parser.add_argument('-w', '--workflow-definition', required=False, help="workflow definition")
    compile_parser.add_argument('-u', '--unum-template', required=False, help="Unum template file")
    compile_parser.add_argument('-o', '--optimize', required=False, choices=['trim'], help="optimizations")

    # build command parser
    build_parser = subparsers.add_parser("build", description="build unum application in the current directory")
    build_parser.add_argument('-p', '--platform', choices=['aws', 'azure'],
        help="target platform", required=False)
    build_parser.add_argument("-g", "--generate", help="Generate a platform template before buliding",
        required = False, action="store_true")
    build_parser.add_argument('-t', '--template',
        help="unum template file", required=False)
    build_parser.add_argument('-s', '--platform_template',
        help="platform template file", required=False)
    build_parser.add_argument("-c", "--clean", help="Remove build artifacts",
        required=False, action="store_true")

    # template command parser
    template_parser = subparsers.add_parser("template", description="generate platform specific template")
    template_parser.add_argument('-p', '--platform', choices=['aws', 'azure'],
        help="target platform", required=False)
    template_parser.add_argument('-t', '--template',
        help="unum template file", required=False)
    template_parser.add_argument("-c", "--clean", help="Remove build artifacts",
        required=False, action="store_true")

    # deploy command parser
    deploy_parser = subparsers.add_parser("deploy", description="deploy unum application")
    deploy_parser.add_argument('-b', '--build', help="build before deploying. Note: does NOT generate new platform template as in unum_cli build -g",
        required=False, action="store_true")
    deploy_parser.add_argument('-p', '--platform', choices=['aws', 'azure'],
        help="target platform", required=False)
    deploy_parser.add_argument('-t', '--template',
        help="unum template file", required=False)
    deploy_parser.add_argument('-s', '--platform_template',
        help="platform template file", required=False)

    # invoke command parser
    invoke_parser = subparsers.add_parser("invoke", description="invoke unum application")
    invoke_parser.add_argument('-p', '--platform', choices=['aws', 'azure'],
        help="target platform", required=False)
    invoke_parser.add_argument('-t', '--template',
        help="unum template file", required=False)

    args = parser.parse_args()

    if args.command =='init':
        unum_init(args)
    elif args.command == 'compile':
        unum_compile(args)
    elif args.command == 'build':
        unum_build(args)
    elif args.command == 'template':
        unum_template(args)
    elif args.command == 'deploy':
        unum_deploy(args)
    elif args.command == 'invoke':
        unum_invoke(args)
    else:
        raise IOError(f'Unknown command: {args.command}')


if __name__ == '__main__':
    rc = 1
    try:
        main()
        rc = 0
    except Exception as e:
        logger.error('Error: %s' % e, file=sys.stderr)
    sys.exit(rc)
