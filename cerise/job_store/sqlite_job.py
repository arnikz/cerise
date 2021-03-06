from .job_state import JobState

class SQLiteJob:
    """This class provides the internal representation of a job. These
    are stored inside the service. Note that there is also a JobDescription,
    which is defined in the Swagger definition and part of the REST API,
    and a Xenon Job class, which represents a job running on the remote
    compute resource.
    """
    def __init__(self, store, job_id):
        """Creates a new SQLiteJob object.

        This contains only a job id and a reference to the store; the
        data about the job are in the database.

        Args:
            store (SQLiteJobStore): The store this job is stored by
            id (str): The id of the job, a string containing a GUID
        """
        self._store = store
        """SQLiteJobStore: A reference to the store this job is in."""

        self.id = job_id
        """str: Job id, a string containing a UUID."""

    # General description
    @property
    def name(self):
        """str: Name, as specified by the submitter.
        """
        return self._get_var('name')

    @property
    def workflow(self):
        """str: Workflow file URI, as specified by the submitter.
        """
        return self._get_var('workflow')

    @property
    def local_input(self):
        """str: Input JSON string, as specified by the submitter.
        """
        return self._get_var('local_input')

    # Current status
    @property
    def state(self):
        """JobState: Current state of the job.
        """
        state_str = self._get_var('state')
        ret = JobState[state_str]
        return JobState[state_str]

    @state.setter
    def state(self, value):
        self._set_var('state', value.name)

    @property
    def please_delete(self):
        """bool: Whether the job should be deleted.
        """
        return bool(self._get_var('please_delete'))

    @please_delete.setter
    def please_delete(self, value):
        self._set_var('please_delete', value)


    @property
    def log(self):
        """str: Log output as of last update.
        """
        return self._get_var('log')

    @log.setter
    def log(self, value):
        self._set_var('log', value)


    @property
    def remote_output(self):
        """str: cwl-runner output as of last update.
        """
        return self._get_var('remote_output')

    @remote_output.setter
    def remote_output(self, value):
        self._set_var('remote_output', value)


    # Post-resolving data
    @property
    def workflow_content(self):
        """Union[bytes, NoneType]: The content of the workflow
        description file, or None if it has not been resolved yet.
        """
        return self._get_var('workflow_content')

    @workflow_content.setter
    def workflow_content(self, value):
        self._set_var('workflow_content', value)


    # Post-staging data
    @property
    def remote_workdir_path(self):
        """str: The absolute remote path of the working directory.
        """
        return self._get_var('remote_workdir_path')

    @remote_workdir_path.setter
    def remote_workdir_path(self, value):
        self._set_var('remote_workdir_path', value)


    @property
    def remote_workflow_path(self):
        """str: The absolute remote path of the CWL workflow file.
        """
        return self._get_var('remote_workflow_path')

    @remote_workflow_path.setter
    def remote_workflow_path(self, value):
        self._set_var('remote_workflow_path', value)


    @property
    def remote_input_path(self):
        """str: The absolute remote path of the input description file.
        """
        return self._get_var('remote_input_path')

    @remote_input_path.setter
    def remote_input_path(self, value):
        self._set_var('remote_input_path', value)


    @property
    def remote_stdout_path(self):
        """str: The absolute remote path of the standard output dump.
        """
        return self._get_var('remote_stdout_path')

    @remote_stdout_path.setter
    def remote_stdout_path(self, value):
        self._set_var('remote_stdout_path', value)


    @property
    def remote_stderr_path(self):
        """str: The absolute remote path of the standard error dump.
        """
        return self._get_var('remote_stderr_path')

    @remote_stderr_path.setter
    def remote_stderr_path(self, value):
        self._set_var('remote_stderr_path', value)


    # Post-destaging data
    @property
    def local_output(self):
        """str: The serialised JSON output object describing the
                destaged outputs.
        """
        return self._get_var('local_output')

    @local_output.setter
    def local_output(self, value):
        self._set_var('local_output', value)


    # Internal data
    @property
    def remote_job_id(self):
        """str: The id the remote scheduler gave to this job.
        """
        return self._get_var('remote_job_id')

    @remote_job_id.setter
    def remote_job_id(self, value):
        self._set_var('remote_job_id', value)


    def try_transition(self, from_state, to_state):
        """Attempts to transition the job's state to a new one.

        If the current state equals from_state, it is set to to_state,
        and True is returned, otherwise False is returned and the
        current state remains what it was.

        Args:
            from_state (JobState): The expected current state
            to_state (JobState): The desired next state

        Returns:
            True iff the transition was successful.
        """
        res = self._store._thread_local_data.conn.execute("""
            UPDATE jobs SET state = ? WHERE job_id = ? AND state = ?;""",
            (to_state.name, self.id, from_state.name))
        self._store._thread_local_data.conn.commit()
        return res.rowcount == 1

    def _get_var(self, var):
        """Do NOT feed this user input for var. Static strings only."""
        res = self._store._thread_local_data.conn.execute("""
            SELECT %s FROM jobs WHERE job_id = ?""" % var,
            (self.id,))
        return res.fetchone()[0]

    def _set_var(self, var, value):
        """Do NOT feed this user input for var. Static strings only."""
        self._store._thread_local_data.conn.execute("""
            UPDATE jobs SET %s = ? WHERE job_id = ?""" % var,
            (value, self.id))
        self._store._thread_local_data.conn.commit()
