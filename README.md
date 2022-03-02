# ![Unum](https://github.com/LedgeDash/unum/blob/main/docs/assets/logo.png "Unum Logo")

Unum is a system for building and running large FaaS applications that consist of many FaaS functions.

A key differentiator of Unum is the ability to run complex FaaS applications without relying on orchestrator services (e.g., AWS Step Functions), while offering the same conveniences and guarantees of state-of-the-art orchestrators.

Application developers can express applications as AWS Step Functions state machines. Unum compiles the orchestration logic in the state machine into an intermediate representation and distributes it to the Lambda functions. During execution, a Unum runtime wraps each function and provides orchestration, error handling and exactly-once execution guarantees in a decentralized fashion, all without a separate orchestrator service.

[//]: # (Unum supports all patterns from AWS Step Functions. Including: List here. What are the other orchestrators with additional patterns not covered by Step Functions?)

[//]: # (Current Unum implementation supports Python functions on AWS.)

To see examples of Unum applications, visit the [Unum application repo](https://github.com/LedgeDash/unum-appstore).

# Getting Started

App developers can write serverless applications with Unum similarly to [AWS SAM](https://aws.amazon.com/serverless/sam/): [Component functions]() each lives inside its own directory and a [Unum template file]() lists all resources in the application and specifies a set of global configurations. Application logic is written as an AWS Step Functions state machine using the [Amazon State Language](https://states-language.net/spec.html).

## Unum Applications

Practically, a typical Unum application with three FaaS functions written in Python would look something like the following:

```text
myapp/
 |- unum-template.yaml
 |- unum-step-functions.json
 |- function1/
   |- app.py
   |- requirements.txt
 |- function2/
   |- app.py
   |- requirements.txt
 |- function3/
   |- app.py
   |- requirements.txt
```

`unum-template.yaml` is the Unum template for the application, `unum-step-functions.json` is the application definition written as AWS Step Functions, and each component FaaS function has its own directory where the app logic lives in `app.py` and any Python dependencies are listed in `requirements.txt`. If you are familiar with writing serverless applications on AWS, you might notice that this programming interface with Unum resembles that of regular AWS Lambda and Step Functions if you are using developement tools such as AWS SAM or CloudFormation.

## Building and Deploying

To bulid and deploy your serverless application, use the `unum-cli`. `unum-cli` does the following 

1. Based on the Step Functions state machine, derives an intermediate representation that decentralizes the orchestration logic in the state machine to component functions (`function1`, `function2`, and `function3` in the example).
2. Load platform-specific Unum runtime into each component function to create the executables for the target platform. During execution, the Unum runtime interposes on application logic (i.e., `app.py`) to provide orchestration, error handling and exactly-once execution guarantee.
3. Generate a platform-specific template (e.g., AWS SAM template) from the Unum template
4. Deploy the application to target platform
5. Rebuild and redeploy the application after making code changes

Current implementation supports Python Lambda functions on AWS and applications are deployed as CloudFormation stacks.

To build an unum application for AWS, run the following command the in an unum
application directory:

```bash
unum-cli build -t -w unum-step-functions.json -p aws
```

The `-t` option would generate an AWS CloudFormation template (named
`template.yaml`) based on `unum-template.yaml` on the fly. You can also
generate a `template.yaml` without building the application by running

```bash
unum-cli template -p aws
```

With the `template.yaml` in the directory, you can simply run

```bash
unum-cli build
```

to build the application for AWS. `unum-cli build` internally calls [AWS SAM](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/what-is-sam.html) to build the application and you will see the build artifacts under the `.aws-sam/` directory.

To deploy your application to AWS, run

```bash
unum-cli deploy
```

If you want to build before deploying, use the following command to combine the two actions

```bash
unum-cli deploy -b
```

Without the `-b` option, unum will try to deploy the existing build artifacts
and you might see `No changes to deploy` because your code changes haven't
been built yet.

`unum-cli deploy` internally calls AWS SAM to deploy the application as an AWS CloudFormation stack. Before calling `unum-cli deploy` for AWS, make sure that you have the environment set up to work with AWS and SAM. 
