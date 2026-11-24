var rule = {
    title: '小心儿悠悠',
    host: 'https://v.qq.com',
    homeUrl: '/x/bu/pagesheet/list?_all=1&append=1&channel=cartoon&listpage=1&offset=0&pagesize=21&iarea=-1&sort=18',
    detailUrl: 'https://node.video.qq.com/x/api/float_vinfo2?cid=fyid',
    searchUrl: 'https://pbaccess.video.qq.com/trpc.videosearch.mobile_search.MultiTerminalSearch/MbSearch?vplatform=2',
    searchable: 1,
    filterable: 1,
    multi: 1,
    url: '/x/bu/pagesheet/list?_all=1&append=1&channel=fyclass&listpage=1&offset=((fypage-1)*21)&pagesize=21&iarea=-1',
    filter_url: 'sort={{fl.sort or 75}}&iyear={{fl.iyear}}&year={{fl.year}}&itype={{fl.type}}&ifeature={{fl.feature}}&iarea={{fl.area}}&itrailer={{fl.itrailer}}&gender={{fl.sex}}',
    filter: 'H4sIAAAAAAAAA+1YW08TWxR+92fMMySdFlrwEY3RnOT4YnzQ8DDBOaERqamVHGJIWksvXFIoYvEcSuViDxVpKYpYprb+mdl7Zv6Fe9rurrXdqE2OlRd4mvnW7L3u31r0mTIxGQpO6MrV+8+Uh/qsclV5EgpHlAFlWnvEUIVm1oixwt5ntKmn7c+mXTgfteJlF2YvgWFlbqCL01y1g4/4ujgpNuyjeY6ryty4K2krDM7qWhg0krMTs74naSSJkhMvdW4YZDcMXGkLvB6vvwO3HrFgGARgI3sZAnwI4z7AfRj3Au7FuAq4inEP4B6Eq6NdnD0ifATwEYwHAA9gHBxW/RgHf9kji/H4gBKZ6UtuA6Pn59a1B+X2L12LPA3roNM6rpPCUg/ZbeNW+pjGE/xuULnyxt7jMGSEPj+0ctkODAl04g1Se96BId+kckoM7ie4SXJ5srDfgSG65udtUkxx70Fl+T+zuc0LGy5JbtINbiCEim7F7eoSdwdKw9l6S7INjiP3M1VSKXAcebS1Rl8VOQ4uWcsZMFIFn6z9LDmroxRx/F2BLkY5HkB659H3I/1t18tuhW59FJoJXigZR2Yf/49uXTyyGgdyoy0eky8bUrcKjaZ62B8q5HjZ3okimR+0CF3eOgfZEfnClX2vCZhIRcwRW6XRHJZ5kPkla104h0KcfmUaC1gfZFjgl9Y5qArmuGgnqlTWwd/YCXcKdNOSIVLMJMnKe3wnyOxozFpI4XNQDnbxC2QTwZUqabwUT7n9gYrlkgn6xwQzWjioR2b7wgVukC4Z/ffkcUILR0Kh6YvkdC2sayi9+SpZNnrmdJJMsBPSBkY3ijR/KG9gh/tWMyNxCUmcmnVOr0O/cODgPQVNAcz0UGAieSLrkmvOP28loxm30xwfWygDqwXrMCsHJLbEho+0HzrldTDlO4sXqlFxtgVwuEmtJi92dHuHbHZnDLo//9o0DLTA8e8zJWs1KS+O4uxAkayvkETtx/l0x+glm/wmNpkMTj3ozz90aHHBXPLNyH+i/430RfdJ+l3vVFL8QMq8z1ATr38C2Nsn5vLhVjqPucSS/klFO815WvnYsyWeQR95H5McHxr0AwyWBAZHAQa7VQ9DzXrRrC3KG3aiJFjVpqBWxTwITYT6UzAijwcjYS04peM4pQzyIkZzZz3HaWzsmhQkstlwF+l8FXgL/L45dpvHSUV2/nnzDzm/LYZzdrNO7IXE0rfu3OX3DPsQG2d2rZNd4QzTA+rvXb8BMPSPnWjalSM7dUDSpzJlk9W02cjTvEELvMf851fh8K8blMwYa/5Uih+b1c7e6+6CD/ibpvn5X6kE2UihuZRstDA8UP9tmsaSlAg2nGH0AT/aJ7ts2MiTieUAhjP+SaNxQHfSUibFXzTQYrKRoDBAW/09PvcVk1nUEv4UAAA=',
    headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://v.qq.com/'
    },
    timeout: 8000,
    cate_exclude: '会员|游戏|全部',
    class_name: '电影&电视剧&综艺&动漫&少儿&纪录片',
    class_url: 'movie&tv&variety&cartoon&child&doco',
    limit: 21,
    play_parse: true, // 关键修改：改为 true，启用解析
    // 移除原 lazy 字段，或保留但不再返回空数组
    推荐: '.list_item;img&&alt;img&&src;a&&Text;a&&data-float',
    一级: '.list_item;img&&alt;img&&src;a&&Text;a&&data-float',
    二级: $js.toString(() => {
        VOD = {};
        let d = [];
        let html = request(input, { headers: rule.headers });
        let cid = input.split("cid=")[1];
        try {
            let json = JSON.parse(html);
            VOD = {
                vod_url: input,
                vod_name: json.c.title,
                type_name: json.typ ? json.typ.join(",") : "",
                vod_actor: json.nam ? json.nam.join(",") : "",
                vod_year: json.c.year,
                vod_content: json.c.description,
                vod_remarks: json.rec,
                vod_pic: json.c.pic
            };
            // 增强：兼容 video_ids 可能为字符串或数组的情况
            let video_lists = json.c.video_ids || [];
            if (typeof video_lists === 'string') {
                video_lists = [video_lists];
            }
            if (video_lists.length > 0) {
                for(let vid of video_lists){
                    // 使用 video_ids 中的值作为 vid 来构造网页播放页地址
                    let playUrl = `https://v.qq.com/x/cover/${cid}/${vid}.html`;
                    d.push({
                        title: "正片",
                        url: playUrl
                    });
                }
            }
            // 将网页地址交给 TVBox 的解析器处理
            VOD.vod_play_from = "腾讯视频";
            VOD.vod_play_url = d.map(it => it.title + "$" + it.url).join("#");
        } catch (e) {
            log("二级解析异常：" + e.message);
        }
    }),
    搜索: $js.toString(() => {
        let d = [];
        let keyword = input;
        let seenIds = new Set();
        function vodSearch(keyword, page = 0) {
            return request(rule.searchUrl, {
                body: JSON.stringify({
                    version: "25042201",
                    clientType: 1,
                    filterValue: "",
                    uuid: "B1E50847-D25F-4C4B-BBA0-36F0093487F6",
                    retry: 0,
                    query: keyword,
                    pagenum: page,
                    isPrefetch: true,
                    pagesize: 30,
                    queryFrom: 0,
                    isneedQc: true,
                    extraInfo: { isNewMarkLabel: "1", multi_terminal_pc: "1", themeType: "1" }
                }),
                headers: {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    'Content-Type': 'application/json',
                    'Origin': 'https://v.qq.com',
                    'Referer': 'https://v.qq.com/'
                },
                method: 'POST'
            });
        }
        const nonMainContentKeywords = ['特辑','片花','独家','专访','纯享','幕后','宣传','看点','主题曲','插曲','彩蛋','精彩','集锦','盘点','解说'];
        function isMainContent(title) {
            if (!title) return false;
            return !nonMainContentKeywords.some(keyword => title.includes(keyword));
        }
        try {
            let html = vodSearch(keyword, 0);
            let json = JSON.parse(html);
            function processItemList(itemList) {
                if (!itemList) return;
                itemList.forEach(it => {
                    if (it.doc && it.doc.id && it.videoInfo && isMainContent(it.videoInfo.title)) {
                        const itemId = it.doc.id;
                        if (!seenIds.has(itemId)) {
                            seenIds.add(itemId);
                            d.push({
                                title: it.videoInfo.title,
                                img: it.videoInfo.imgUrl || "",
                                url: itemId, // 这里的 itemId 将作为二级的 input，可能不是 cid，需要检查
                                desc: it.videoInfo.secondLine || ""
                            });
                        }
                    }
                });
            }
            if (json.data && json.data.normalList) {
                processItemList(json.data.normalList.itemList);
            }
            if (json.data && json.data.areaBoxList) {
                json.data.areaBoxList.forEach(box => {
                    processItemList(box.itemList);
                });
            }
        } catch (e) {
            log("搜索出错: " + e.message);
        }
        setResult(d);
    })
};
