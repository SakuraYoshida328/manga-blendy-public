import argparse
import uvicorn
import json
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(description="Manga Blendy local application")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument('--projects',type=Path,help='資料集の保存フォルダ。省略時はアプリのprojects')
    commands=parser.add_subparsers(dest='command')
    search=commands.add_parser('list');search.add_argument('--query',default='')
    master=commands.add_parser('master');master.add_argument('background_id');master.add_argument('--state')
    request=commands.add_parser('request');request.add_argument('background_id');request.add_argument('request_json',type=Path);request.add_argument('--state');request.add_argument('--run',action='store_true')
    export=commands.add_parser('export-image');export.add_argument('background_id');export.add_argument('job_id');export.add_argument('--camera')
    diagnose=commands.add_parser('diagnose')
    handoff=commands.add_parser('finish-export');handoff.add_argument('background_id');handoff.add_argument('job_id')
    args = parser.parse_args(argv)
    if args.command:
        from .config import APP_ROOT,load_config
        from .storage import Store,read_json,safe_path
        from .domain import DomainError
        store=Store(args.projects or APP_ROOT/'projects')
        from .integration import find_backgrounds,get_master,request_background,export_image_bundle
        from .jobs import JobManager,run_job
        try:
            if args.command=='list':result={'backgrounds':find_backgrounds(store,args.query)}
            elif args.command=='master':result=get_master(store,args.background_id,args.state)
            elif args.command=='diagnose':
                from .diagnostics import check_environment
                result=check_environment(load_config()['blender_path'])
            elif args.command=='request':
                manager=JobManager(store)
                result=request_background(store,manager,args.background_id,args.state,read_json(args.request_json))
                if args.run:
                    run_job(str(store.root),args.background_id,result['job_id'])
                    result['job']=manager.job(args.background_id,result['job_id'])
                    result['job'].pop('config',None)
            elif args.command=='export-image':
                directory=export_image_bundle(store,args.background_id,args.job_id,args.camera)
                result={'image':str(directory/'background.png'),'metadata':str(directory/'background.json')}
            else:
                from .finishing import export_handoff
                folder=safe_path(store.project(args.background_id),f'jobs/{args.job_id}/output/finish')
                result={'handoff_zip':str(export_handoff(folder))}
            print(json.dumps(result,ensure_ascii=False,indent=2))
        except DomainError as exc:
            print(json.dumps({'error':{'code':exc.code,'message':str(exc)}},ensure_ascii=False))
            raise SystemExit(1)
        return
    from .web.app import create_app
    uvicorn.run(create_app(args.projects), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
