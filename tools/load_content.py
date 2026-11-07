"""C-06-01: 검증한 콘텐츠를 새 버전으로만 Mongo에 적재한다."""
import argparse
import os
from pathlib import Path
from server.infra.content_store import MongoContentSource
from tools.check_content import content_problems, load_bundle
from tools.jsonlog import log


def main(argv=None):
    """환경의 서비스 연결로 새 콘텐츠를 적재한다. URI·본문은 로그에 쓰지 않는다."""
    parser=argparse.ArgumentParser()
    parser.add_argument('bundle',type=Path)
    args=parser.parse_args(argv)
    client=None
    try:
        bundle=load_bundle(args.bundle)
        if content_problems(bundle): raise ValueError('content_references_invalid')
        from pymongo import MongoClient
        client=MongoClient(os.environ['PALDO_MONGO_URI'],serverSelectionTimeoutMS=5000,timeoutMS=10000)
        database=client.get_default_database()
        MongoContentSource(database['content_bundles']).insert(bundle)
        log('load_content','info','content_version_inserted',content_version=bundle.content_version)
    except Exception as error:
        log('load_content','error','content_insert_failed',error_type=type(error).__name__)
        raise SystemExit(1) from None
    finally:
        if client is not None: client.close()


if __name__=='__main__':main()
