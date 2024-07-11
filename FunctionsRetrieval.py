import json
import os
from sklearn.metrics.pairwise import cosine_similarity
from sklearn.feature_extraction.text import TfidfVectorizer # type: ignore
import numpy as np
from BlocksCutting import function_block
from rank_bm25 import BM25Okapi
import openai
import argparse

class block(function_block):
    def __init__(self, file_path_,name=None, b_class=None, s_line=None, e_line=None, calls=None, import_list=None):
        super().__init__(name, b_class, s_line, e_line, calls, import_list)
        self.file_path=file_path_

    def __repr__(self):
        return (f"FunctionBlock(name={self.name}, class_name={self.belong_class}, "
                f"lineno={self.start_line}, end_lineno={self.end_line}, calls={self.call_func},import={self.import_repo},file_path={self.file_path})")

def load_function_blocks(json_file):
    with open(json_file, 'r') as f:
        data = json.load(f)

    function_blocks = []
    class_methods = {}

    for file_path, contents in data.items():
        class_methods[file_path] = {}

        for func in contents["Functions"]:
            function_block = block(
                file_path_=file_path,
                name=func['name'],
                b_class=func['class'],
                s_line=func['lineno'],
                e_line=func['end_lineno'],
                calls=func['calls'],
                import_list=func['import']
            )
            function_blocks.append(function_block)

        for cls in contents["Classes"]:
            class_name = cls['class_name']
            methods = cls['methods']# methods 已经是一个 list
            class_methods[file_path][class_name] = methods

    #print(class_methods)
    return function_blocks, class_methods

def load_query(query_file):
    with open(query_file, 'r') as f:
        query = f.read()
    return query

def get_function_text(block):
    file_path = block.file_path
    start_line = block.start_line
    end_line = block.end_line
    full_path = os.path.join(root_dir, file_path)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"File not found: {full_path}")
        return ""
    except Exception as e:
        print(f"Error reading file: {full_path}, Error: {e}")
        return ""

    function_text = ''.join(lines[start_line-1:end_line])
    return function_text

def compute_bm25_similarity(query, function_blocks):
    texts = [get_function_text(block) for block in function_blocks]
    tokenized_texts = [text.split() for text in texts]
    bm25 = BM25Okapi(tokenized_texts)

    query_tokens = query.split()
    scores = bm25.get_scores(query_tokens)
    return scores

def compute_tfidf_similarity(query, function_blocks):
    texts = [get_function_text(block) for block in function_blocks]
    texts.append(query)
    
    vectorizer = TfidfVectorizer()
    tfidf_matrix = vectorizer.fit_transform(texts)
    
    query_vector = tfidf_matrix[-1]
    function_vectors = tfidf_matrix[:-1]
    #变成向量之后展平
    scores = (function_vectors*query_vector.T).toarray().flatten()
    return scores

def jaccard_similarity(query, document):
    query_set = set(query.split())
    document_set = set(document.split())
    intersection = query_set.intersection(document_set)
    union = query_set.union(document_set)
    return len(intersection) / len(union)

def compute_jaccard_similarity(query, function_blocks):
    similarities = []
    for block in function_blocks:
        text = get_function_text(block)
        similarity = jaccard_similarity(query, text)
        similarities.append(similarity)
    return similarities

#openai.api_key = 'your_openai_api_key'
def get_embedding(text, model="text-embedding-ada-002"):
    response = openai.Embedding.create(
        input=text,
        model=model
    )
    return response['data'][0]['embedding']

def compute_openai_similarity(query, function_blocks):
    query_embedding = get_embedding(query)
    similarities = []
    for block in function_blocks:
        text = get_function_text(block)
        text_embedding = get_embedding(text)
        similarity = cosine_similarity(query_embedding, text_embedding)
        similarities.append(similarity)
    return similarities

def lexical_ranking(
        query,
        docs,
        ranking_fn,#排序方式
        top_n,
        doc_ids=None,#如果为 true 会淘汰非常小的检索结果
        score_threshold=None,
):  
    '''这个函数根据不同的排序方法（如 bm25、tfidf、jaccard_sim）对文档进行排序。可以选择是否使用评分阈值来过滤文档，并按评分对文档进行排序。'''
    if ranking_fn == "bm25":
        scores = compute_bm25_similarity(query, docs)
    elif ranking_fn == "tfidf":
        scores = compute_tfidf_similarity(query, docs)
    elif ranking_fn == "jaccard_sim":
        scores = compute_jaccard_similarity(query,docs)
    elif ranking_fn == "openai":
        scores=compute_openai_similarity(query,docs)
    else:
        raise NotImplementedError

    #print(scores)
    if score_threshold is not None:
        print("Use score threshold!")
        #根据阈值筛选
        skip_ids = [idx for idx, s in enumerate(scores) if s < score_threshold]
        scores = [s for idx, s in enumerate(scores) if idx not in skip_ids]
        docs = [d for idx, d in enumerate(docs) if idx not in skip_ids]
        if doc_ids is not None:
            doc_ids = [doc_id for idx, doc_id in enumerate(doc_ids) if idx not in skip_ids]
        #所有值都被跳过
        if len(docs) == 0:
            return np.zeros(1,top_n)
    #根据之前求出来的值排序
    top_n_indices = np.argsort(scores)[-top_n:][::-1]
    top_n_blocks = [docs[i] for i in top_n_indices]
    return top_n_blocks


def get_class_method(certain_block:block,class_dict,function_blocks):
    ret=[]
    class_name=certain_block.belong_class
    if class_name:
        methods=class_dict[certain_block.file_path][class_name]
        for method in methods:
            for block in function_blocks:
                if block.name==method and block.belong_class == class_name:
                    ret.append(block)
    return ret

def get_call_blocks(certain_block,function_blocks):
    ret = []
    func_inside=certain_block.call_func
    for block in function_blocks:
        # 检查函数名是否在调用列表中
        for call in func_inside:
            # 如果调用是类内方法
            if "." in call:
                class_name, method_name = call.split(".", 1)
                if block.class_name == class_name and block.name == method_name:
                    ret.append(block)
            else:
                # 如果调用是类外函数或库函数
                if block.name == call and (not block.belong_class):
                    ret.append(block)
                elif block.name == call.split(".")[-1] and call.split(".")[0] in block.import_repo:
                    ret.append(block)#这里是针对其他相关文件中的 函数
                    #这里没有包括 库函数 ，相信在提供库后LLM可以找到。

    return ret

def main(json_file, query_file,rank_fn, top_n=2,relative_methods_num=None,relative_calls_num=None,if_tell_import=None):
    function_blocks,class_methods = load_function_blocks(json_file)
    query = load_query(query_file)
    top_n_blocks =lexical_ranking(query,function_blocks,rank_fn,top_n)
    cnt=0
    print(f"In total there are {len(function_blocks)} functions. This time you are provided with {top_n} most similar functions and relative information about them.")
    print("You can refer the code below, they are from the python library or the same repository of the query.\n")
    for block in top_n_blocks:
        cnt+=1
        print('#'*70)
        print(f"Number:{cnt} most similar function.\n{get_function_text(block)}")
        if (not relative_calls_num) and (not relative_methods_num) and (not if_tell_import):
            continue
        
        if block.import_repo:
            print('-'*50)
            print(f"The function {cnt} is in the file {block.file_path}.\nThis python file import {block.import_repo}")
        
        if block.belong_class and relative_methods_num>0:
            print('-'*50)
            print(f"The code below are the methods belong to the class of function {cnt}!")
            relative_blocks=get_class_method(block,class_methods,function_blocks)
            for methods in relative_blocks:
                print(f"File: {methods.file_path}\ncontext:\n{get_function_text(methods)}")
        relative_methods_num-=1

        if block.call_func and relative_calls_num>0:
            print('-'*50)
            print(f"The code below are the functions the function {cnt} calls!")
            relative_blocks=get_call_blocks(block,function_blocks)
            for func in relative_blocks:
                print(f"File: {func.file_path}\ncontext:\n{get_function_text(func)}")
        relative_calls_num-=1


#main("F:/trydir/parsed_code_tryfilesss.json","F:/trydir/query.py","bm25",top_num,1,1,1)


if __name__ == "__main__":
    json_file = input("Please enter the path to the JSON file with parsed code: ")
    query_file = input("Please enter the path to the file containing the query code: ")
    root_dir = input("Please enter the root directory for code files: ")
    rank_fn = input("Please enter the ranking function to use (bm25, tfidf, jaccard_sim, openai): ")
    top_num = int(input("Please enter the number of top results to return: "))
    relative_numbers = input("Please enter the relative_methods_num, relative_calls_num, and if_tell_import (separated by spaces): ")
    arg1,arg2,arg3= map(int, relative_numbers.split())


main(json_file, query_file,rank_fn,top_num,arg1,arg2,arg3)
    
