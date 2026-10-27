.PHONY: demo serve test docker docker-run score

demo:            ## keyless: replay the recorded judge, score it against human labels
	python -m evalkit demo

serve:           ## API at http://127.0.0.1:8000/docs (replay mode unless ANALYSIS_PROVIDER is set)
	ANALYSIS_PROVIDER=$${ANALYSIS_PROVIDER:-replay} python -m evalkit serve

test:
	python -m pytest -q tests
	python tests/test_recording_notice.py
	python tests/test_end_call_guard.py

score:           ## score the newest tracked judge run
	python -m evalkit score

docker:
	docker build -t llm-voice-agent-eval .

docker-run: docker
	docker run --rm -p 8000:8000 llm-voice-agent-eval
