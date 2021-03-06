import webdav.client as wc
from webdav.exceptions import LocalResourceNotFound
from bravado.client import SwaggerClient
from bravado.exception import HTTPNotFound
from bravado_core.formatter import SwaggerFormat

import docker
import json
import os
import pytest
import requests
import time

@pytest.fixture(scope="module")
def slurm_docker_image(request):
    client = docker.from_env()
    image = client.images.build(
            path='integration_test/xenon_slurm_docker/xenon-phusion-base',
            tag='xenon-phusion-base')

    image = client.images.build(
            path='integration_test/xenon_slurm_docker/xenon-slurm/',
            tag='xenon-slurm')

    image = client.images.build(
            path='integration_test/',
            dockerfile='test_slurm.Dockerfile',
            rm=True,
            tag='cerise-integration-test-slurm-image')

#    image = client.images.get('cerise-integration-test-slurm-image')
    return image.id

def clear_old_container(name):
    client = docker.from_env()
    # Remove any stale containers so that we can rebuild the image
    try:
        old_container = client.containers.get(name)
        old_container.stop()
        old_container.remove()
    except docker.errors.NotFound:
        pass

def clear_old_containers():
    clear_old_container('cerise-integration-test-container')
    clear_old_container('cerise-integration-test-slurm')

def make_docker_image(tag, path, dockerfile):
    client = docker.from_env()
    image = client.images.build(
            path=path,
            dockerfile=dockerfile,
            rm=True,
            tag=tag)

    return image.id

@pytest.fixture(scope="module")
def service_docker_image(request):
    clear_old_containers()
    make_docker_image('cerise', '.', 'Dockerfile')
    return make_docker_image('cerise-integration-test-image',
            'integration_test/', 'test_service.Dockerfile')

@pytest.fixture()
def docker_client(request):
    return docker.from_env()

@pytest.fixture
def service(request, tmpdir, docker_client, slurm_docker_image, service_docker_image):
    # Start SLURM docker
    slurm_container = docker_client.containers.run(
            slurm_docker_image,
            name='cerise-integration-test-slurm',
            detach=True)
    time.sleep(1)   # Give it some time to start up

    # Start service docker
    cur_dir = os.path.dirname(__file__)
    api_dir = os.path.join(cur_dir, 'api')
    cerise_container = docker_client.containers.run(
            service_docker_image,
            name='cerise-integration-test-container',
            links={ 'cerise-integration-test-slurm':
                'cerise-integration-test-slurm' },
            ports={ '29593/tcp': ('127.0.0.1', 29593) },
            detach=True)
    time.sleep(2)   # Give it some time to start up

    yield cerise_container

    # Collect logs for debugging
    archive_file = os.path.join(str(tmpdir), 'docker_logs.tar')
    stream, _ = cerise_container.get_archive('/var/log')
    with open(archive_file, 'wb') as f:
        f.write(stream.read())

    # Collect run dir for debugging
    archive_file = os.path.join(str(tmpdir), 'docker_run.tar')
    stream, stat = cerise_container.get_archive('/home/cerise/run')
    with open(archive_file, 'wb') as f:
        f.write(stream.read())

    # Collect jobs dir for debugging
    try:
        archive_file = os.path.join(str(tmpdir), 'docker_jobs.tar')
        stream, stat = slurm_container.get_archive('/home/xenon/.cerise/jobs')
        with open(archive_file, 'wb') as f:
            f.write(stream.read())
    except docker.errors.NotFound:
        pass

    # Collect API dir for debugging
    try:
        archive_file = os.path.join(str(tmpdir), 'docker_api.tar')
        stream, stat = slurm_container.get_archive('/home/xenon/.cerise/api')
        with open(archive_file, 'wb') as f:
            f.write(stream.read())
    except docker.errors.NotFound:
        pass

    # Tear down
    cerise_container.stop()
    cerise_container.remove()

    slurm_container.stop()
    slurm_container.remove()

@pytest.fixture
def webdav_client(request, service):
    return wc.Client({'webdav_hostname': 'http://localhost:29593'})

@pytest.fixture
def service_client(request, service):
    # Disable Bravado warning about uri format not being registered
    # It's all done by frameworks, so we're not testing that here
    uri_format = SwaggerFormat(
            description='A Uniform Resource Identifier',
            format='uri',
            to_wire=lambda uri: uri,
            to_python=lambda uri: uri,
            validate=lambda uri_string: True
    )

    bravado_config = {
        'also_return_response': True,
        'formats': [uri_format]
        }

    return SwaggerClient.from_url('http://localhost:29593/swagger.json', config=bravado_config)

def _create_test_job(name, cwlfile, inputfile, files, webdav_client, service):
    """
    Creates a job for the test cases to work with.

    Args:
        name (str): Name of the test job
        cwlfile (str): Name of the CWL file to use (in current dir)
        inputfile (str): Name of input file to use
        files ([[str, str]]: List of name, filename pairs to stage
        webdav_client (wc.Client): WebDAV client fixture
        service (SwaggerClient): REST client fixture
    """
    input_dir = '/files/input/' + name
    webdav_client.mkdir(input_dir)

    cur_dir = os.path.dirname(__file__)
    test_workflow = os.path.join(cur_dir, cwlfile)
    remote_workflow_path = input_dir + '/' + cwlfile
    webdav_client.upload_sync(local_path = test_workflow, remote_path = remote_workflow_path)

    test_input = os.path.join(cur_dir, inputfile)
    with open(test_input, 'r') as f:
        input_data = json.load(f)

    for name, filename in files:
        input_file = os.path.join(cur_dir, filename)
        remote_path = input_dir + '/' + filename
        try:
            webdav_client.upload_sync(local_path = input_file, remote_path = remote_path)
        except LocalResourceNotFound:
            # May be missing as part of the test, so log, but continue
            print("Local file missing in _create_test_job")
        input_data[name] = {
                "class": "File",
                "basename": filename,
                "location": 'http://localhost:29593/' + remote_path
                }

    JobDescription = service.get_model('job-description')
    job_desc = JobDescription(
        name=name,
        workflow='http://localhost:29593' + remote_workflow_path,
        input=input_data
    )
    (job, response) = service.jobs.post_job(body=job_desc).result()
    print(str(response))
    assert response.status_code == 201
    return job

def _wait_for_finish(job_id, timeout, service):
    """
    Polls the service for the job's status, returning when
    it's done or until a timeout.

    Args:
        job_id (str): The id of the job to wait for
        timeout (int): Number of seconds to time out after
        service (SwaggerClient): REST client fixture

    Returns:
        (Job): The finished job, or None if it timed out.
    """
    test_job, _ = service.jobs.get_job_by_id(jobId=job_id).result()
    count = 0
    while (test_job.state == 'Waiting' or test_job.state == 'Running') and count < timeout:
        time.sleep(0.5)
        test_job, _ = service.jobs.get_job_by_id(jobId=job_id).result()
        count += 0.5
    if count >= timeout:
        return None
    else:
        return test_job

def test_cancel_job_by_id(webdav_client, service_client):
    """
    Test case for cancel_job_by_id

    Cancel a job
    """
    test_job = _create_test_job('test_cancel_job_by_id',
            'slow_job.cwl', 'null_input.json', [],
            webdav_client, service_client)

    # Wait for it to start
    time.sleep(1)

    # Cancel test job
    (_, response) = service_client.jobs.cancel_job_by_id(jobId=test_job.id).result()

    time.sleep(2)

    # Check that state is now cancelled
    (updated_job, response) = service_client.jobs.get_job_by_id(jobId=test_job.id).result()
    assert response.status_code == 200
    assert updated_job.state == 'Cancelled'

def test_delete_job_by_id(service, webdav_client, service_client):
    """
    Test case for delete_job_by_id

    Delete a job.
    """
    test_job = _create_test_job('test_delete_job_by_id',
            'test_workflow.cwl', 'test_input.json', [],
            webdav_client, service_client)

    service_client.jobs.delete_job_by_id(jobId=test_job.id).result()

    time.sleep(10.0)

    with pytest.raises(HTTPNotFound):
        service_client.jobs.get_job_by_id(jobId=test_job.id).result()


def test_get_job_by_id(service, webdav_client, service_client):
    """
    Test case for get_job_by_id

    Get a job
    """
    test_job = _create_test_job('test_get_job_by_id',
            'test_workflow.cwl', 'test_input.json', [],
            webdav_client, service_client)

    time.sleep(10.0)
    (job, _) = service_client.jobs.get_job_by_id(jobId=test_job.id).result()

    print(job)
    assert job.name == test_job.name
    assert job.workflow == test_job.workflow
    assert job.input == test_job.input
    assert job.state == 'Success'

    out_file_location = job.output['output']['location']
    print(out_file_location)
    out_data = requests.get(out_file_location)
    assert out_data.status_code == 200
    assert out_data.text == 'Hello world!\n'


def test_get_job_log_by_id(service, webdav_client, service_client):
    """
    Test case for get_job_log_by_id

    Log of a job
    """
    _create_test_job('test_get_job_log_by_id',
            'test_workflow.cwl', 'test_input.json', [],
            webdav_client, service_client)

#   Disable the following for now, Bravado only supports JSON responses, and
#   this is plain text :(

#   time.sleep(1)
#   (log, response) = service_client.jobs.get_job_log_by_id(jobId=test_job.id).result()
#   assert response.status_code == 200


def test_get_jobs(service, service_client):
    """
    Test case for get_jobs

    list of jobs
    """
    (jobs, response) = service_client.jobs.get_jobs().result()
    assert jobs == []
    assert response.status_code == 200


def test_post_job(service, webdav_client, service_client):
    """
    Test case for post_job

    submit a new job
    """
    test_job = _create_test_job('test_post_job',
            'test_workflow.cwl', 'test_input.json', [],
            webdav_client, service_client)

    assert test_job.state == 'Waiting'

def test_post_staging_job(service, webdav_client, service_client):
    """
    Tests running a job that requires (de)staging input and output.
    """
    test_job = _create_test_job('test_post_staging_job',
            'staging_workflow.cwl', 'null_input.json',
            [('file', 'hello_world.txt')],
            webdav_client, service_client)

    test_job = _wait_for_finish(test_job.id, 20, service_client)
    assert test_job.state == 'Success'

    out_data = requests.get(test_job.output['output']['location'])
    assert out_data.status_code == 200
    assert out_data.text.startswith(' 4 11 58 ')
    assert out_data.text.endswith('hello_world.txt\n')

def test_post_api_job(service, webdav_client, service_client):
    """
    Tests running a job that uses the files/ part of the API.
    """
    test_job = _create_test_job('test_post_api_job',
            'test_api.cwl', 'null_input.json', [],
            webdav_client, service_client)

    test_job = _wait_for_finish(test_job.id, 20, service_client)

    assert test_job.state == 'Success'
    out_data = requests.get(test_job.output['output']['location'])
    assert out_data.status_code == 200
    assert out_data.text.startswith('Running on host:')

def test_post_missing_job(service, service_client):
    """
    Tests submitting a job referencing a non-existant CWL file.
    """
    JobDescription = service_client.get_model('job-description')
    job_desc = JobDescription(
        name='test_post_missing_job',
        workflow='http://localhost:29593/files/does_not_exist/no_really.cwl',
        input={}
    )
    (job, _) = service_client.jobs.post_job(body=job_desc).result()
    job = _wait_for_finish(job.id, 20, service_client)
    assert job.state == 'PermanentFailure'

def test_post_broken_job(service, webdav_client, service_client):
    """
    Tests running a job that runs a non-existing command.
    """
    test_job = _create_test_job('test_post_broken_job',
            'broken_workflow.cwl', 'null_input.json', [],
            webdav_client, service_client)

    test_job = _wait_for_finish(test_job.id, 20, service_client)
    assert test_job.state == 'PermanentFailure'

def test_post_missing_input(service, webdav_client, service_client):
    """
    Tests posting a job with an input object referencing a file that
    does not exist.
    """
    test_job = _create_test_job('test_post_missing_input',
            'staging_workflow.cwl', 'null_input.json',
            [('file', 'does_not_exist.txt')],
            webdav_client, service_client)

    test_job = _wait_for_finish(test_job.id, 20, service_client)
    assert test_job.state == 'PermanentFailure'

def test_failure_partial_output(service, webdav_client, service_client):
    """
    Tests running a job that fails and produces partial output. We want
    to have whatever is produced back in that case.
    """
    test_job = _create_test_job('test_failure_partial_output',
            'partial_failure.cwl', 'null_input.json',
            [], webdav_client, service_client)

    test_job = _wait_for_finish(test_job.id, 20, service_client)
    assert test_job.state == 'PermanentFailure'

    out_data = requests.get(test_job.output['output']['location'])
    assert out_data.status_code == 200

    assert 'missing_output' not in test_job.output

def test_post_commandline_tool(service, webdav_client, service_client):
    """
    Tests posting a job with a CWL CommandLineTool process, which is
    not allowed and should fail.
    """
    test_job = _create_test_job('test_post_commandline_tool',
            'sleep.cwl', 'null_input.json',
            [], webdav_client, service_client)
    test_job = _wait_for_finish(test_job.id, 20, service_client)
    assert test_job.state == 'PermanentFailure'

def test_post_jobs_with_same_name(service, webdav_client, service_client):
    """
    Test case for post_job

    submit a new job
    """
    test_job = _create_test_job('test_post_jobs_with_same_name',
            'test_workflow.cwl', 'test_input.json', [],
            webdav_client, service_client)

    test_job2 = _create_test_job('test_post_jobs_with_same_name',
            'test_workflow.cwl', 'test_input.json', [],
            webdav_client, service_client)

    test_job = _wait_for_finish(test_job.id, 20, service_client)
    test_job2 = _wait_for_finish(test_job2.id, 20, service_client)

    assert test_job.state == 'Success'
    assert test_job2.state == 'Success'

def test_restart_service(service, webdav_client, service_client):
    """
    Tests stopping and restarting the service with jobs running.
    """
    test_job = _create_test_job('test_restart_service',
            'slow_job.cwl', 'null_input.json', [],
            webdav_client, service_client)

    time.sleep(2)
    service.stop()
    time.sleep(3)
    service.start()
    time.sleep(1)       # Trying to connect immediately crashes test, why?
    test_job = _wait_for_finish(test_job.id, 20, service_client)
    assert test_job.state == 'Success'

def test_api_install_script(service, webdav_client, service_client):
    """
    Tests whether the api install script ran successfully.
    """
    test_job = _create_test_job('test_api_install_script',
            'test_api_install.cwl', 'null_input.json', [],
            webdav_client, service_client)

    test_job = _wait_for_finish(test_job.id, 20, service_client)

    print(test_job)
    assert test_job.state == 'Success'
    out_data = requests.get(test_job.output['output']['location'])
    assert out_data.status_code == 200
    assert out_data.text == 'Test\n'

