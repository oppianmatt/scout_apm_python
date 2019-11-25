# coding=utf-8
from __future__ import absolute_import, division, print_function, unicode_literals

import datetime as dt

import wrapt
from rq import SimpleWorker as RqSimpleWorker
from rq import Worker as RqWorker
from rq.job import Job

import scout_apm.core
from scout_apm.compat import utc
from scout_apm.core.tracked_request import TrackedRequest

install_attempted = False
installed = None


def ensure_scout_installed():
    global install_attempted, installed

    if not install_attempted:
        install_attempted = True
        installed = scout_apm.core.install()


class WorkerMixin(object):
    def __init__(self, *args, **kwargs):
        global installed
        ensure_scout_installed()
        if installed:
            ensure_job_instrumented()
        super(WorkerMixin, self).__init__(*args, **kwargs)


class Worker(WorkerMixin, RqWorker):
    pass


class SimpleWorker(WorkerMixin, RqSimpleWorker):
    pass


job_instrumented = False


def ensure_job_instrumented():
    global job_instrumented
    if job_instrumented:
        return
    job_instrumented = True
    Job.perform = wrap_perform(Job.perform)


@wrapt.decorator
def wrap_perform(wrapped, instance, args, kwargs):
    global installed
    if not installed:
        return wrapped(*args, **kwargs)

    tracked_request = TrackedRequest.instance()
    tracked_request.is_real_request = True
    tracked_request.tag("task_id", instance.get_id())
    tracked_request.tag("queue", instance.origin)
    # rq stores UTC datetime as naive, we always use timezone aware datetimes
    enqueued_at_utc = instance.enqueued_at.replace(tzinfo=utc)
    queue_time = (dt.datetime.now(tz=utc) - enqueued_at_utc).total_seconds()
    tracked_request.tag("queue_time", queue_time)
    tracked_request.start_span(operation="Job/{}".format(instance.func_name))
    try:
        return wrapped(*args, **kwargs)
    except Exception:
        tracked_request.tag("error", "true")
        raise
    finally:
        tracked_request.stop_span()
